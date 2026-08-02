"""Whisper(+LoRA) acoustic-prior encoder for the ASA scorer (Option A flagging, RoPE).

Ported from LOSS-ASA's `WhisperAudioEncoder` (keep every frame, concatenate chunks, pool
with a learnable `[CLS]` Transformer -> one vector per scoring unit). Additions for ASA:

  * `task_emb`   — learned embedding per task/part/ROLE. SANDI: part P1/P3/P4/P5; Finnish:
                   task_id 01-06; dialogue (future): speaker role mono / dialogue-A / -B.
  * `answer_emb` — learned per-frame "which segment" embedding (SANDI: answer Q1..Qk;
                   dialogue: turn index), computed EXACTLY at frame resolution from the
                   clip/segment-boundary times the dataset passes in.

Both are ADDED to the frames before the aggregator (Stage 2), like positional encoding —
not masks. For single-segment data (DTA monologue) the index is constant 0 -> `answer_emb`
inert -> byte-identical to the proven LOSS-ASA aggregator. Position encoding is RoPE
(relative, length-free — good for long/variable dialogue streams). Whisper encoder runs at
50 frames/sec (1500/30s). Returns (B, D).

Dialogue-ready (NOT implemented here): a per-speaker stream IS a monologue, so dialogue
reuses this encoder unchanged — just gather one speaker's diarized turns as the segment list
(role via `task_id`, turns via segment boundaries). Embedding tables are sized generously so
dialogue turn counts / roles fit without resizing.
"""

import contextlib

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import WhisperModel

WHISPER_ENC_FPS = 50.0   # encoder frames per second (1500 frames / 30 s chunk)


# --------------------------------------------------------------------------- RoPE
def rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1, x2 = x.chunk(2, dim=-1)
    return torch.cat((-x2, x1), dim=-1)


class RotaryEmbedding(nn.Module):
    def __init__(self, head_dim: int, base: float = 10000.0):
        super().__init__()
        inv_freq = 1.0 / (base ** (torch.arange(0, head_dim, 2).float() / head_dim))
        self.register_buffer("inv_freq", inv_freq, persistent=False)

    def forward(self, seq_len: int, device, dtype):
        t = torch.arange(seq_len, device=device, dtype=self.inv_freq.dtype)
        freqs = torch.outer(t, self.inv_freq)
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos().to(dtype), emb.sin().to(dtype)


def apply_rope(q, k, cos, sin):
    cos, sin = cos[None, None, :, :], sin[None, None, :, :]
    return (q * cos) + (rotate_half(q) * sin), (k * cos) + (rotate_half(k) * sin)


class RoPESelfAttention(nn.Module):
    def __init__(self, d_model, n_heads, dropout):
        super().__init__()
        assert d_model % n_heads == 0
        self.n_heads, self.head_dim, self.dropout = n_heads, d_model // n_heads, dropout
        self.q_proj = nn.Linear(d_model, d_model)
        self.k_proj = nn.Linear(d_model, d_model)
        self.v_proj = nn.Linear(d_model, d_model)
        self.out_proj = nn.Linear(d_model, d_model)
        self.rotary = RotaryEmbedding(self.head_dim)

    def forward(self, x, key_padding_mask=None):
        B, T, D = x.shape
        q = self.q_proj(x).view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        cos, sin = self.rotary(T, x.device, x.dtype)
        q, k = apply_rope(q, k, cos, sin)
        attn_mask = None
        if key_padding_mask is not None:
            attn_mask = torch.zeros(B, 1, 1, T, device=x.device, dtype=q.dtype)
            attn_mask.masked_fill_(key_padding_mask[:, None, None, :], float("-inf"))
        out = F.scaled_dot_product_attention(
            q, k, v, attn_mask=attn_mask, dropout_p=self.dropout if self.training else 0.0)
        out = out.transpose(1, 2).contiguous().view(B, T, D)
        return self.out_proj(out)


class RoPEEncoderLayer(nn.Module):
    def __init__(self, d_model, n_heads, dim_feedforward, dropout):
        super().__init__()
        self.norm1 = nn.LayerNorm(d_model)
        self.attn = RoPESelfAttention(d_model, n_heads, dropout)
        self.norm2 = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, dim_feedforward), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(dim_feedforward, d_model))
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, key_padding_mask=None):
        x = x + self.dropout(self.attn(self.norm1(x), key_padding_mask))
        x = x + self.ffn(self.norm2(x))
        return x


class RoPEEncoder(nn.Module):
    def __init__(self, n_layers, d_model, n_heads, dim_feedforward, dropout):
        super().__init__()
        self.layers = nn.ModuleList(
            [RoPEEncoderLayer(d_model, n_heads, dim_feedforward, dropout) for _ in range(n_layers)])
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x, src_key_padding_mask=None):
        for layer in self.layers:
            x = layer(x, src_key_padding_mask)
        return self.norm(x)


# ------------------------------------------------------------------ acoustic encoder
class WhisperAcousticEncoder(nn.Module):
    def __init__(
        self,
        whisper_name: str = "openai/whisper-medium",
        max_chunks: int = 4,               # 4 * 30s = 120s (dialogue: raise to ~8)
        frame_pool: int = 2,
        n_layers: int = 2,
        n_heads: int = 8,
        dim_feedforward: int = 2048,
        dropout: float = 0.1,
        pos_encoding: str = "rope",        # "rope" (default) | "sinusoidal"
        lora: bool = True,
        lora_r: int = 16,
        lora_alpha: int = 32,
        lora_dropout: float = 0.05,
        lora_target: str = "attn_ffn",
        attn_impl: str = "sdpa",           # "sdpa" (flash kernels, no extra pkg) | "flash_attention_2" | "eager"
        grad_checkpoint: bool = True,
        n_tasks: int = 16,                 # task/part/role ids (generous for dialogue roles)
        n_answers: int = 32,               # segment/turn ids (generous for dialogue turns)
        use_task_emb: bool = True,
        use_answer_emb: bool = True,
        layer_sum: bool = False,       # learnable weighted sum over all encoder layers (vs last only)
        cache_dir: str = "/scratch/elec/t412-slaam/hf_cache/hub",
    ):
        super().__init__()
        self.whisper = WhisperModel.from_pretrained(
            whisper_name, cache_dir=cache_dir, attn_implementation=attn_impl)
        self.hidden_size = self.whisper.config.d_model
        self.max_chunks = max_chunks
        self.frame_pool = frame_pool
        self.n_answers = n_answers
        self.use_task_emb = use_task_emb
        self.use_answer_emb = use_answer_emb

        # Default: use the final encoder layer only (the proven v5e path). Optionally learn a
        # softmax-weighted sum over ALL encoder hidden states (embeddings + every layer) so the
        # aggregator can draw on mid-depth acoustic/phonetic layers, not just the ASR-final layer.
        self.layer_sum = layer_sum
        if layer_sum:
            self.layer_weights = nn.Parameter(torch.zeros(self.whisper.config.encoder_layers + 1))

        if lora:
            from peft import LoraConfig, get_peft_model
            targets = {
                "attn": ["q_proj", "k_proj", "v_proj", "out_proj"],
                "ffn": ["fc1", "fc2"],
                "attn_ffn": ["q_proj", "k_proj", "v_proj", "out_proj", "fc1", "fc2"],
            }[lora_target]
            self.whisper = get_peft_model(
                self.whisper,
                LoraConfig(r=lora_r, lora_alpha=lora_alpha, lora_dropout=lora_dropout,
                           target_modules=targets, bias="none"))
        if grad_checkpoint:
            self.whisper.gradient_checkpointing_enable(
                gradient_checkpointing_kwargs={"use_reentrant": False})
            if hasattr(self.whisper, "enable_input_require_grads"):
                self.whisper.enable_input_require_grads()

        self.task_emb = nn.Embedding(n_tasks, self.hidden_size) if use_task_emb else None
        self.answer_emb = nn.Embedding(n_answers, self.hidden_size) if use_answer_emb else None
        for emb in (self.task_emb, self.answer_emb):
            if emb is not None:
                nn.init.zeros_(emb.weight)            # start inert; learn the conditioning

        self.cls_token = nn.Parameter(torch.zeros(1, 1, self.hidden_size))
        nn.init.normal_(self.cls_token, std=0.02)

        self.pos_encoding = pos_encoding
        if pos_encoding == "rope":
            self.pos_enc = None
            self.aggregator = RoPEEncoder(n_layers, self.hidden_size, n_heads, dim_feedforward, dropout)
        elif pos_encoding == "sinusoidal":
            self.pos_enc = _Sinusoidal(self.hidden_size)
            layer = nn.TransformerEncoderLayer(
                d_model=self.hidden_size, nhead=n_heads, dim_feedforward=dim_feedforward,
                dropout=dropout, batch_first=True, norm_first=True)
            self.aggregator = nn.TransformerEncoder(layer, num_layers=n_layers)
        else:
            raise ValueError(f"pos_encoding must be 'rope' or 'sinusoidal', got {pos_encoding!r}")

    def train(self, mode: bool = True):
        super().train(mode)
        if not any(p.requires_grad for p in self.whisper.parameters()):
            self.whisper.eval()
        return self

    def _whisper_features(self, feats: torch.Tensor) -> torch.Tensor:
        frozen = not any(p.requires_grad for p in self.whisper.parameters())
        with torch.no_grad() if frozen else contextlib.nullcontext():
            if self.layer_sum:
                hs = self.whisper.encoder(feats, output_hidden_states=True).hidden_states
            else:
                return self.whisper.encoder(feats).last_hidden_state      # (C, 1500, D)
        # Weighted sum outside the frozen block so layer_weights still receive gradient.
        stacked = torch.stack(hs, dim=0)                                  # (L+1, C, 1500, D)
        w = torch.softmax(self.layer_weights, dim=0).to(stacked.dtype)
        return (w.view(-1, 1, 1, 1) * stacked).sum(0)                     # (C, 1500, D)

    def _encode_chunks(self, feats: torch.Tensor, bounds: torch.Tensor | None):
        feats = feats[: self.max_chunks]
        hidden = self._whisper_features(feats)                            # (C, 1500, D)
        C, T, D = hidden.shape
        hidden = hidden.reshape(C * T, D)
        if self.frame_pool > 1:
            hidden = hidden.transpose(0, 1).unsqueeze(0)
            hidden = F.avg_pool1d(hidden, self.frame_pool, self.frame_pool)
            hidden = hidden.squeeze(0).transpose(0, 1)                    # (Tp, D)

        Tp = hidden.shape[0]
        if self.use_answer_emb and bounds is not None and bounds.numel() > 0:
            t = torch.arange(Tp, device=hidden.device) * (self.frame_pool / WHISPER_ENC_FPS)
            ans = torch.searchsorted(bounds.to(hidden.device), t, right=True).clamp(max=self.n_answers - 1)
        else:
            ans = torch.zeros(Tp, dtype=torch.long, device=hidden.device)
        return hidden, ans

    def forward(self, input_features, clip_bounds=None, task_ids=None, return_frames=False):
        """input_features: list[B] of (C_i,80,3000);  clip_bounds: list[B] of (k_i,) cumulative
        segment end-times in seconds (or None);  task_ids: (B,) long or None.
        Returns (B, D) — the CLS acoustic prior. If return_frames=True, additionally returns
        the contextualized per-frame sequence and its padding mask:
        (cls (B,D), frames (B,T,D), frame_pad_mask (B,T) True=pad) — for frame-level dim heads
        (e.g. the Finnish multidim scorer). English callers omit the flag -> unchanged."""
        device = next(self.parameters()).device
        if clip_bounds is None:
            clip_bounds = [None] * len(input_features)

        seqs, ans_ids = [], []
        for feats, b in zip(input_features, clip_bounds):
            s, a = self._encode_chunks(feats.to(device), None if b is None else b.to(device))
            seqs.append(s)
            ans_ids.append(a)

        lengths = [s.shape[0] for s in seqs]
        T_max, B, D = max(lengths), len(seqs), self.hidden_size
        padded = torch.zeros(B, T_max, D, device=device, dtype=seqs[0].dtype)
        ans_pad = torch.zeros(B, T_max, dtype=torch.long, device=device)
        pad_mask = torch.ones(B, T_max, dtype=torch.bool, device=device)
        for i, (s, a) in enumerate(zip(seqs, ans_ids)):
            padded[i, : lengths[i]] = s
            ans_pad[i, : lengths[i]] = a
            pad_mask[i, : lengths[i]] = False

        # Stage-2 flag: ADD answer + task/role embeddings to the frames (not masks).
        if self.answer_emb is not None:
            padded = padded + self.answer_emb(ans_pad).to(padded.dtype)
        if self.task_emb is not None and task_ids is not None:
            padded = padded + self.task_emb(task_ids.to(device)).unsqueeze(1).to(padded.dtype)

        cls = self.cls_token.expand(B, 1, D).to(padded.dtype)
        x = torch.cat([cls, padded], dim=1)
        mask = torch.cat([torch.zeros(B, 1, dtype=torch.bool, device=device), pad_mask], dim=1)
        if self.pos_enc is not None:
            x = self.pos_enc(x)
        x = self.aggregator(x, src_key_padding_mask=mask)
        if return_frames:
            # x[:, 0] = CLS prior; x[:, 1:] = contextualized per-frame states; pad_mask True=pad
            return x[:, 0], x[:, 1:], pad_mask
        return x[:, 0]                                                    # (B, D) acoustic prior


class _Sinusoidal(nn.Module):
    def __init__(self, d_model: int):
        super().__init__()
        self.d_model = d_model

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        import math
        _, T, D = x.shape
        pos = torch.arange(T, device=x.device).unsqueeze(1)
        div = torch.exp(torch.arange(0, D, 2, device=x.device) * (-math.log(10000.0) / D))
        pe = torch.zeros(T, D, device=x.device)
        pe[:, 0::2], pe[:, 1::2] = torch.sin(pos * div), torch.cos(pos * div)
        return x + pe.unsqueeze(0)
