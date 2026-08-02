"""Fused Whisper-LoRA acoustic + Qwen-LoRA reasoning scorer (the ASA model).

Per scoring unit (SANDI: one speaker x part):
  acoustic branch  ─► acoustic prior a (B, Dw)
                       │ MLP projector                  │ aux_head (Linear)
                       ▼                                ▼
  a ─► k soft tokens (B, k, Dq) ──┐          aux_logits (acoustic-only)
                                  ├─ prepend ─► Qwen3.5-LoRA(inputs_embeds) ─► hidden @ last token
  tokenized prompt (B, L, Dq) ────┘                                            │ head (Linear)
                                                                               ▼
                                                                     logits (full model)

`logits` returned as (B, 2): col 0 = full model, col 1 = acoustic-only aux head.
This lets metrics compare acoustic-branch RMSE vs full model RMSE in one training run.

Loss = MSE(logits[:,0], score) + aux_weight * auxiliary_loss.
By default auxiliary_loss is MSE. With `aux_tolerance > 0`, it is squared
distance beyond a zero-loss band around the target.
`aux_weight=0` disables the aux head loss (aux logits still returned for monitoring).
`use_acoustic=False` drops both soft tokens and aux head (text-only ablation).
"""

import math

import torch
import torch.nn as nn
from transformers import AutoConfig, AutoModelForCausalLM

from .whisper_acoustic import WhisperAcousticEncoder

HF_CACHE = "/scratch/elec/t412-slaam/hf_cache/hub"


def build_acoustic_encoder(acoustic_kwargs: dict | None):
    """Pick the acoustic backbone by the requested model's family. whisper -> log-mel
    WhisperAcousticEncoder; wav2vec2/wavlm/hubert-family -> raw-waveform SSLAcousticEncoder.
    Both expose the same (B, D) contract, so the rest of the scorer is agnostic."""
    kw = dict(acoustic_kwargs or {})
    name = kw.get("whisper_name", "openai/whisper-medium")
    try:
        model_type = AutoConfig.from_pretrained(name, cache_dir=HF_CACHE).model_type
    except Exception:
        model_type = "whisper"
    if model_type == "whisper":
        return WhisperAcousticEncoder(**kw)
    from .ssl_acoustic import SSLAcousticEncoder
    return SSLAcousticEncoder(**kw)


def tolerance_mse_loss(prediction: torch.Tensor, target: torch.Tensor,
                       tolerance: float,
                       sample_weights: torch.Tensor | None = None) -> torch.Tensor:
    """Squared distance outside target +/- tolerance; ordinary MSE at tolerance 0."""
    excess = (prediction - target).abs().sub(tolerance).clamp_min(0)
    squared = excess.square()
    if sample_weights is not None:
        squared = squared * sample_weights
    return squared.mean()


class QwenAcousticScorer(nn.Module):
    def __init__(
        self,
        qwen_name: str = "Qwen/Qwen3.5-2B",
        acoustic_kwargs: dict | None = None,
        n_soft_tokens: int = 4,
        use_acoustic: bool = True,
        inject_aux_score: bool = True,  # add the Whisper-LoRA CEFR estimate as its own soft token
        aux_weight: float = 0.3,        # weight for acoustic-only aux loss; 0 = monitor only
        main_weight: float = 1.0,       # weight for the full-model main loss (schedulable at train)
        aux_tolerance: float = 0.0,     # zero aux loss inside target +/- this value
        detach_aux_acoustic: bool = False,  # prevent aux loss from updating Whisper/aggregator
        detach_soft_tokens: bool = False,   # stop MAIN loss reaching Whisper/aggregator: the acoustic
                                            # branch is then optimized SOLELY by the aux head; the
                                            # projector still learns (main loss) to present it to Qwen
        acoustic_projector_dropout: float = 0.0,
        normalize_acoustic_projector: bool = False,
        acoustic_gate_init: float | None = None,
        score_head_init_mean: float | None = None,
        spec_augment_probability: float = 0.0,
        spec_augment_freq_max: int = 0,
        spec_augment_time_max: int = 0,
        qwen_lora_r: int = 16,
        qwen_lora_alpha: int = 32,
        qwen_lora_dropout: float = 0.05,
        qwen_lora_target=("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"),
        attn_impl: str = "sdpa",        # "sdpa" (flash kernels, no extra pkg) | "flash_attention_2" | "eager"
        grad_checkpoint: bool = True,
        cache_dir: str = HF_CACHE,
    ):
        super().__init__()
        self.use_acoustic = use_acoustic
        self.n_soft = n_soft_tokens
        self.inject_aux_score = inject_aux_score and use_acoustic
        self.aux_weight = aux_weight
        self.main_weight = main_weight
        if aux_tolerance < 0:
            raise ValueError("aux_tolerance must be non-negative")
        self.aux_tolerance = aux_tolerance
        self.detach_aux_acoustic = detach_aux_acoustic
        self.detach_soft_tokens = detach_soft_tokens
        if not 0.0 <= spec_augment_probability <= 1.0:
            raise ValueError("spec_augment_probability must be between 0 and 1")
        if spec_augment_freq_max < 0 or spec_augment_time_max < 0:
            raise ValueError("SpecAugment mask widths must be non-negative")
        self.spec_augment_probability = spec_augment_probability
        self.spec_augment_freq_max = spec_augment_freq_max
        self.spec_augment_time_max = spec_augment_time_max

        # --- acoustic branch -------------------------------------------------
        self.acoustic = build_acoustic_encoder(acoustic_kwargs)
        d_acoustic = self.acoustic.hidden_size

        # --- Qwen reasoning branch ------------------------------------------
        # bf16 base weights (fits an 80G GPU comfortably). peft keeps LoRA adapters in fp32
        # (autocast_adapter_dtype default) so AdamW stays stable. Needs Ampere+ (cc>=80).
        self.qwen = AutoModelForCausalLM.from_pretrained(
            qwen_name, cache_dir=cache_dir, dtype=torch.bfloat16, attn_implementation=attn_impl)
        d_qwen = self.qwen.config.hidden_size
        from peft import LoraConfig, get_peft_model
        self.qwen = get_peft_model(
            self.qwen,
            LoraConfig(r=qwen_lora_r, lora_alpha=qwen_lora_alpha, lora_dropout=qwen_lora_dropout,
                       target_modules=list(qwen_lora_target), bias="none", task_type="CAUSAL_LM"))
        if grad_checkpoint:
            self.qwen.gradient_checkpointing_enable(
                gradient_checkpointing_kwargs={"use_reentrant": False})
            if hasattr(self.qwen, "enable_input_require_grads"):
                self.qwen.enable_input_require_grads()

        # --- fusion + heads --------------------------------------------------
        if use_acoustic:
            self.projector_input_norm = (
                nn.LayerNorm(d_acoustic) if normalize_acoustic_projector else nn.Identity())
            self.projector = nn.Sequential(
                nn.Linear(d_acoustic, d_qwen), nn.GELU(),
                nn.Linear(d_qwen, n_soft_tokens * d_qwen))
            # Kept outside Sequential so adding dropout does not renumber projector
            # parameters and break loading of pre-dropout v1-v3 checkpoints.
            self.projector_dropout = nn.Dropout(acoustic_projector_dropout)
            self.projector_output_norm = (
                nn.LayerNorm(d_qwen) if normalize_acoustic_projector else nn.Identity())
            if acoustic_gate_init is not None:
                if not 0.0 < acoustic_gate_init < 1.0:
                    raise ValueError("acoustic_gate_init must be between 0 and 1")
                gate_logit = math.log(acoustic_gate_init / (1.0 - acoustic_gate_init))
                self.acoustic_gate_logit = nn.Parameter(torch.tensor(gate_logit))
            else:
                self.register_parameter("acoustic_gate_logit", None)
            # Auxiliary head: reads directly from acoustic prior, bypassing Qwen entirely.
            # Trained jointly; its RMSE = what Whisper-LoRA alone can achieve (LOSS-ASA style).
            self.aux_head = nn.Linear(d_acoustic, 1)
            # The (detached) Whisper-LoRA CEFR estimate is rendered as a REAL NUMBER text line
            # ("acoustic_cefr_estimate: X.X") injected into the prompt at forward time, so Qwen
            # literally reads the acoustic branch's score. Needs a tokenizer to render it.
            if self.inject_aux_score:
                from transformers import AutoTokenizer
                self._htok = AutoTokenizer.from_pretrained(qwen_name, cache_dir=cache_dir)
                if self._htok.pad_token is None:
                    self._htok.pad_token = self._htok.eos_token
        self.head = nn.Linear(d_qwen, 1)   # full model head (acoustic + Qwen)
        if score_head_init_mean is not None:
            nn.init.zeros_(self.head.weight)
            nn.init.constant_(self.head.bias, score_head_init_mean)
            if use_acoustic:
                nn.init.zeros_(self.aux_head.weight)
                nn.init.constant_(self.aux_head.bias, score_head_init_mean)
        self.d_qwen = d_qwen
        self.loss_fn = nn.MSELoss()

    def _embed_tokens(self, input_ids):
        return self.qwen.get_input_embeddings()(input_ids)

    def _render_score_lines(self, aux_logits, device, dtype):
        """Render each (detached) Whisper-LoRA score as a real-number text line and embed it:
        "acoustic_cefr_estimate: X.X\n\n". Returns (emb (B,H,Dq), mask (B,H))."""
        vals = aux_logits.detach().clamp(0, 6)                            # CEFR-range for display
        lines = [f"acoustic_cefr_estimate: {v:.1f}\n\n" for v in vals.tolist()]
        enc = self._htok(lines, return_tensors="pt", padding=True)
        ids = enc["input_ids"].to(device)
        mask = enc["attention_mask"].to(device)
        return self._embed_tokens(ids).to(dtype), mask

    def _apply_spec_augment(self, input_features, clip_bounds):
        """Apply one mild frequency/time mask per valid chunk on selected training items."""
        if not self.training or self.spec_augment_probability <= 0:
            return input_features
        if clip_bounds is None:
            clip_bounds = [None] * len(input_features)
        augmented = []
        for features, bounds in zip(input_features, clip_bounds):
            if torch.rand(()) >= self.spec_augment_probability:
                augmented.append(features)
                continue
            masked = features.clone()
            n_chunks, n_mels, n_frames = masked.shape
            duration = (
                float(bounds[-1]) if bounds is not None and bounds.numel() > 0
                else n_chunks * n_frames / 100.0
            )
            for chunk_index in range(n_chunks):
                valid_seconds = max(0.0, duration - chunk_index * n_frames / 100.0)
                valid_frames = min(n_frames, max(1, int(math.ceil(valid_seconds * 100.0))))
                freq_width = int(torch.randint(
                    0, min(self.spec_augment_freq_max, n_mels) + 1, ()).item())
                if freq_width:
                    freq_start = int(torch.randint(0, n_mels - freq_width + 1, ()).item())
                    masked[chunk_index, freq_start:freq_start + freq_width, :] = 0
                time_width = int(torch.randint(
                    0, min(self.spec_augment_time_max, valid_frames) + 1, ()).item())
                if time_width:
                    time_start = int(torch.randint(
                        0, valid_frames - time_width + 1, ()).item())
                    masked[chunk_index, :, time_start:time_start + time_width] = 0
            augmented.append(masked)
        return augmented

    def forward(self, input_ids, attention_mask, input_features=None, clip_bounds=None,
                task_ids=None, scores=None, loss_weights=None, **unused):
        tok_embeds = self._embed_tokens(input_ids)                        # (B, L, Dq)
        attn = attention_mask
        aux_logits = None

        if self.use_acoustic:
            input_features = self._apply_spec_augment(input_features, clip_bounds)
            prior = self.acoustic(input_features, clip_bounds, task_ids)  # (B, Dw)
            # Auxiliary prediction: acoustic prior → score (no Qwen)
            # Optional stop-gradient lets the aux head learn a calibrated readout without
            # allowing its loss to reshape the shared Whisper acoustic representation.
            aux_prior = prior.detach() if self.detach_aux_acoustic else prior
            aux_logits = self.aux_head(
                aux_prior.to(self.aux_head.weight.dtype)).squeeze(-1)  # (B,)
            # Full-model path: project prior to k acoustic soft tokens, prepend to Qwen input.
            # detach_soft_tokens stops the MAIN loss from reshaping the acoustic branch (Whisper +
            # aggregator) via the soft tokens; the branch is then trained only by the aux head, while
            # the projector still adapts (main loss) to render the fixed acoustic summary for Qwen.
            soft_prior = prior.detach() if self.detach_soft_tokens else prior
            projector_input = self.projector_input_norm(soft_prior)
            projected = self.projector[1](self.projector[0](projector_input))
            projected = self.projector_dropout(projected)
            soft = self.projector[2](projected).view(
                prior.size(0), self.n_soft, self.d_qwen)
            soft = self.projector_output_norm(soft)
            if self.acoustic_gate_logit is not None:
                soft = soft * self.acoustic_gate_logit.sigmoid()
            soft = soft.to(tok_embeds.dtype)
            parts = [soft]                                                # (B, k, Dq)
            masks = [torch.ones(attn.size(0), self.n_soft, dtype=attn.dtype, device=attn.device)]
            # inject the Whisper-LoRA CEFR estimate as a REAL-NUMBER text line right after the
            # acoustic soft tokens, before the prompt body
            if self.inject_aux_score:
                s_emb, s_mask = self._render_score_lines(aux_logits, attn.device, tok_embeds.dtype)
                parts.append(s_emb); masks.append(s_mask.to(attn.dtype))
            parts.append(tok_embeds); masks.append(attn)
            inputs_embeds = torch.cat(parts, dim=1)                       # (B, k+H+L, Dq)
            attn = torch.cat(masks, dim=1)
        else:
            inputs_embeds = tok_embeds

        # explicit position_ids keep RoPE correct if the score line introduced any internal padding
        position_ids = (attn.long().cumsum(dim=1) - 1).clamp(min=0)
        out = self.qwen(inputs_embeds=inputs_embeds, attention_mask=attn, position_ids=position_ids,
                        output_hidden_states=True, use_cache=False)
        last_hidden = out.hidden_states[-1]                               # (B, k+H+L, Dq)

        # Readout at the final real token (the "<SCORE>:" cue). Robust to internal padding:
        # last index where attn == 1 (not sum-1, which assumes a single contiguous run).
        last_idx = (attn.size(1) - 1) - attn.flip(1).float().argmax(dim=1)  # (B,)
        pooled = last_hidden[torch.arange(last_hidden.size(0), device=last_hidden.device), last_idx]
        logits = self.head(pooled.to(self.head.weight.dtype)).squeeze(-1)  # (B,)

        # Stack: (B, 2) col 0 = full model, col 1 = acoustic-only aux.
        # Trainer passes this as eval_pred.predictions; metrics.py splits the columns.
        if aux_logits is not None:
            out_logits = torch.stack([logits, aux_logits], dim=1)
        else:
            # text-only mode: pad col 1 with zeros so metrics shape is always (B, 2)
            out_logits = torch.stack([logits, torch.zeros_like(logits)], dim=1)

        loss = None
        if scores is not None:
            tgt = scores.to(logits.device).float()
            main_squared_error = (logits.float() - tgt).square()
            if loss_weights is not None:
                weights = loss_weights.to(logits.device).float()
                main_loss = (main_squared_error * weights).mean()
            else:
                main_loss = main_squared_error.mean()
            if aux_logits is not None and self.aux_weight > 0:
                aux_loss = tolerance_mse_loss(
                    aux_logits.float(), tgt, self.aux_tolerance,
                    sample_weights=weights if loss_weights is not None else None)
                loss = self.main_weight * main_loss + self.aux_weight * aux_loss
            else:
                loss = self.main_weight * main_loss

        return {"loss": loss, "logits": out_logits} if loss is not None else {"logits": out_logits}
