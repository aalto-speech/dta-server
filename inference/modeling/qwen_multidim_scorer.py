"""Multi-dimension decomposition scorer for FINNISH.

Separate file — the English SANDI `QwenAcousticScorer` in qwen_scorer.py is NOT touched;
this subclasses it and swaps the heads + forward.

Instead of predicting holistic CEFR directly, it predicts the 4 CEFR analytic dimensions
(fluency, pronunciation, range=vocabulary, accuracy=grammar) and reconstructs CEFR from
them. Two independent axes give the three experiment configs:

  dim_arch  = how the 4 dimensions are predicted
    "routed" (config 1): HARD modality routing — an acoustic head on the Whisper CLS prior
              (a single pooled vector) predicts [fluency, pronunciation]; a content head on
              the Qwen readout predicts [range, accuracy].
    "routed_frames" (config 1, corrected): same routing, but the acoustic dims read the FULL
              contextualized Whisper FRAME sequence (mean+std pooled over all frames), not the
              single CLS vector — so fluency/pronunciation see temporal detail (pausing,
              rhythm, per-segment spectral variation) the CLS pool discards.
    "gated"  (config 2/3): all 4 dims get BOTH an acoustic estimate (from the prior) and a
              fused estimate (from the Qwen readout); a small learned per-dimension gate
              g in [0,1] blends them:  dim = g*acoustic + (1-g)*fused.

    Cross-channel sharing variants (2026-07 ablation — DTA rubric judges fluency/pron by
    intelligibility of the CONTENT, and range/accuracy may need acoustic context for ASR errors):
    "fused_frames":  routed_frames, but the fluency/pron head ALSO reads the DETACHED Qwen
              pooled readout (content context; the acoustic dim loss never backprops into Qwen).
    "bridge_frames": routed_frames, but the soft tokens read [CLS || frame mean+std] through a
              fusion layer zero-init'd at [I | 0] (starts EXACTLY at routed_frames), so the
              LLM's range/accuracy judgment sees the temporal evidence too.
    "gated_frames":  gated with the acoustic side upgraded from the CLS prior to the frame
              mean+std pool (CLS-only was the known handicap: clsvec 0.518 vs framepool 0.506).
    "shared_pool":   FINAL design (2026-07-17): ONE acoustic summary — the frame mean+std pool —
              with two consumers and NO CLS. (a) fluency/pron head reads [pool || detached Qwen
              readout]; (b) the soft tokens read the SAME pool through bridge_pool_proj. The
              pool gets gradient from both the acoustic dim loss (direct) and the content/CEFR
              loss (through the bridge). bridge_frames is the keep-CLS ablation of this.
    "mcasa":  M-CASA (2026-07-24): NEW per-dimension aggregator query tokens CLS_flu / CLS_pron
              attention-pool the frames; each acoustic head reads a HYBRID input [CLS_d || frame
              mean+std || detached Qwen readout] (learned salience + temporal moments + content
              context). The CASA bridge-CLS (prior -> soft tokens) is UNCHANGED; content head on
              the Qwen readout -> [range, accuracy]. Each CLS_d is trained only by its own dim loss.

    Per-row soft-token masking: batches may carry `no_audio` (B,) 1.0 = text-only synthetic row.
    Those rows get their soft-token positions attention-masked out -> a pure text prompt (no
    fabricated acoustic evidence reaches Qwen, and the projector/trunk receive zero gradient
    from them). Source-separated training: synthetic rows update Qwen LoRA + content head only.

  combine_mode = how the 4 dims map to CEFR
    "formula" (config 1/2): FROZEN OLS formula fit on DTA (true dims -> CEFR).
    "router"  (config 3):  LEARNED nn.Linear(4,1), warm-started at that OLS formula.

Configs:  1 = routed+formula   2 = gated+formula   3 = gated+router

logits returned as (B, 5): col0 = combined CEFR, cols1-4 = [fluency, pronunciation, range,
accuracy] in DIM_ORDER. Loss = dim_loss_weight*MSE(dims, dim_targets)
                              + cefr_loss_weight*MSE(cefr, scores).
"""

import math

import torch
import torch.nn as nn

from .qwen_scorer import QwenAcousticScorer

# order of dim_targets columns and output cols 1-4
DIM_ORDER = ["fluency", "pronunciation", "range", "accuracy"]
# per-dimension warm-start for the gate: acoustic-leaning for fluency/pron, text-leaning
# for range/accuracy (config 2/3 can move these during training)
_GATE_INIT = [0.7, 0.7, 0.3, 0.3]


class QwenMultiDimScorer(QwenAcousticScorer):
    def __init__(self, *args, dim_arch: str = "routed", combine_mode: str = "formula",
                 formula_coef=None, formula_intercept: float = 0.0,
                 dim_loss_weight: float = 1.0, cefr_loss_weight: float = 0.0,
                 combiner_hidden: int = 16, moe_experts: int = 4,
                 dim_spread_weight: float = 0.0,
                 use_soft_tokens: bool = True, **kwargs):
        # dims flow to the LLM via the acoustic soft tokens, not the rendered text line
        kwargs.setdefault("inject_aux_score", False)
        super().__init__(*args, **kwargs)
        if not self.use_acoustic:
            raise ValueError("multidim scorer requires the acoustic branch (use_acoustic=True)")
        if dim_arch not in ("routed", "routed_frames", "fused_frames", "bridge_frames",
                            "gated", "gated_frames", "shared_pool", "mcasa"):
            raise ValueError(
                "dim_arch must be routed/routed_frames/fused_frames/bridge_frames/gated/"
                f"gated_frames/shared_pool/mcasa, got {dim_arch!r}")

        # m-CASA variant: text-only Qwen — the soft-token bridge (CLS -> projector -> Qwen) is
        # skipped; Qwen judges the content dims from the rubric prompt (ASR text + acoustic-
        # evidence text) alone. The acoustic branch still runs for the frame-level dims.
        # The projector params then exist but receive no gradient (like the unused aux head).
        self.use_soft_tokens = bool(use_soft_tokens)

        d_acoustic = self.acoustic.hidden_size
        self.dim_arch = dim_arch
        if dim_arch == "routed":
            self.aux_head = nn.Linear(d_acoustic, 2)        # [fluency, pronunciation]
            self.content_head = nn.Linear(self.d_qwen, 2)   # [range (vocab), accuracy (grammar)]
        elif dim_arch == "routed_frames":
            # acoustic dims from frame-level mean+std pool (2*d_acoustic); content from Qwen
            self.acoustic_frame_head = nn.Linear(2 * d_acoustic, 2)  # [fluency, pronunciation]
            self.content_head = nn.Linear(self.d_qwen, 2)            # [range, accuracy]
        elif dim_arch == "fused_frames":
            # routed_frames + content context: fluency/pron head also reads the DETACHED Qwen
            # pooled readout (rubric: both dims are intelligibility-of-content judgments)
            self.acoustic_frame_head = nn.Linear(2 * d_acoustic + self.d_qwen, 2)
            self.content_head = nn.Linear(self.d_qwen, 2)
        elif dim_arch == "shared_pool":
            # final design: fused_frames heads + a CLS-free bridge — the soft tokens read the
            # same frame mean+std pool through a 2d->d adapter in front of the projector chain
            self.acoustic_frame_head = nn.Linear(2 * d_acoustic + self.d_qwen, 2)
            self.content_head = nn.Linear(self.d_qwen, 2)
            self.bridge_pool_proj = nn.Linear(2 * d_acoustic, d_acoustic)
        elif dim_arch == "mcasa":
            # M-CASA: NEW per-dimension aggregator query tokens CLS_flu / CLS_pron attention-pool
            # the contextualized Whisper frames; each acoustic-dim head reads its own hybrid input
            # [CLS_d || frame mean+std || detached Qwen readout] (learned salience + guaranteed
            # temporal moments + content context). The CASA bridge-CLS (prior -> soft tokens) is
            # UNCHANGED; content head reads the Qwen readout. Each CLS_d gets gradient only from its
            # own dim loss (routed by dim_mask); the detached readout keeps flu/pron off Qwen.
            n_head = next((h for h in (8, 4, 2, 1) if d_acoustic % h == 0), 1)
            self.dim_query = nn.Parameter(torch.randn(2, d_acoustic) * 0.02)   # [CLS_flu, CLS_pron]
            self.dim_pool_attn = nn.MultiheadAttention(d_acoustic, num_heads=n_head,
                                                       batch_first=True)
            self.mcasa_flu_head = nn.Linear(3 * d_acoustic + self.d_qwen, 1)   # flu
            self.mcasa_pron_head = nn.Linear(3 * d_acoustic + self.d_qwen, 1)  # pron
            self.content_head = nn.Linear(self.d_qwen, 2)                      # [range, accuracy]
        elif dim_arch == "bridge_frames":
            # routed_frames heads; the soft-token bridge reads bridge_fusion([CLS || frame
            # mean+std]) instead of the CLS alone. [I | 0] + zero-bias init -> the fused vector
            # starts == CLS, so training starts exactly at routed_frames and can only learn to
            # mix the temporal pool in (same idiom as the zero-init residual combiners).
            self.acoustic_frame_head = nn.Linear(2 * d_acoustic, 2)
            self.content_head = nn.Linear(self.d_qwen, 2)
            self.bridge_fusion = nn.Linear(3 * d_acoustic, d_acoustic)
            with torch.no_grad():
                self.bridge_fusion.weight.zero_()
                self.bridge_fusion.weight[:, :d_acoustic].copy_(torch.eye(d_acoustic))
                self.bridge_fusion.bias.zero_()
        else:  # gated / gated_frames: both branches predict all 4 dims; a learned gate blends
            # per dim. gated reads the CLS prior; gated_frames the frame mean+std pool.
            d_side = d_acoustic if dim_arch == "gated" else 2 * d_acoustic
            self.dim_head_acoustic = nn.Linear(d_side, 4)
            self.dim_head_fused = nn.Linear(self.d_qwen, 4)
            gate_logit = torch.tensor([math.log(g / (1 - g)) for g in _GATE_INIT])
            self.dim_gate_logit = nn.Parameter(gate_logit)  # (4,) sigmoid -> acoustic weight

        self.combine_mode = combine_mode
        self.dim_loss_weight = float(dim_loss_weight)
        self.dim_spread_weight = float(dim_spread_weight)
        self.cefr_loss_weight = float(cefr_loss_weight)

        coef = torch.tensor(
            formula_coef if formula_coef is not None else [0.25, 0.25, 0.25, 0.25],
            dtype=torch.float32)
        # Always keep the DTA-fit OLS as buffers: it's the deployed formula for combine_mode
        # 'formula' AND the residual base for the nonlinear combiners (mlp/moe start == OLS,
        # then learn a correction for the per-band over-low/under-high bias a straight line
        # cannot fix — see docs; the linear 'router' stayed at OLS because it had no such room).
        self.register_buffer("formula_coef", coef)                       # (4,)
        self.register_buffer("formula_intercept", torch.tensor(float(formula_intercept)))
        if combine_mode == "formula":
            self.combiner = None
        elif combine_mode == "router":
            self.combiner = nn.Linear(4, 1)
            with torch.no_grad():   # warm-start the learned linear combiner at the OLS formula
                self.combiner.weight.copy_(coef.view(1, 4))
                self.combiner.bias.fill_(float(formula_intercept))
        elif combine_mode == "mlp":
            # nonlinear RESIDUAL over the OLS: cefr = OLS(dims) + mlp(dims). Last layer zero-init
            # so it starts exactly at OLS (can't hurt) then bends the mapping.
            h = int(combiner_hidden)
            self.combiner = nn.Sequential(nn.Linear(4, h), nn.GELU(), nn.Linear(h, 1))
            nn.init.zeros_(self.combiner[-1].weight)
            nn.init.zeros_(self.combiner[-1].bias)
        elif combine_mode == "moe":
            # mixture of K linear "band experts" + a softmax gate on the dims: a deployable,
            # differentiable soft per-band OLS. cefr = OLS(dims) + sum_k gate_k(dims)*expert_k(dims).
            # experts zero-init -> residual starts == OLS; the gate learns to pick the
            # level-appropriate correction without needing the band a priori.
            k = int(moe_experts)
            self.moe_gate = nn.Linear(4, k)
            self.moe_experts = nn.ModuleList([nn.Linear(4, 1) for _ in range(k)])
            for e in self.moe_experts:
                nn.init.zeros_(e.weight)
                nn.init.zeros_(e.bias)
            self.combiner = None
        elif combine_mode in ("mlpcefr", "mlpcefrx"):
            # cefr = OLS(detach(dims)) + mlp([detach(dims), OLS_estimate(1)( , detach(readout))]).
            # dims are DETACHED into the combiner -> the CEFR loss trains ONLY the combiner; the
            # dim heads + encoders keep their own dim loss (no back-door). v1 (mlpcefr) feeds the
            # dims + the OLS level estimate; v2 (mlpcefrx) also feeds the rich readout (Qwen
            # pooled + acoustic prior) projected to h, so it has info beyond the compressed dims.
            h = int(combiner_hidden)
            in_dim = 4 + 1                                              # [dims(4), ols_estimate(1)]
            if combine_mode == "mlpcefrx":
                self.readout_proj = nn.Sequential(
                    nn.Linear(self.d_qwen + d_acoustic, h), nn.GELU(), nn.Dropout(0.1))
                in_dim += h
            self.combiner = nn.Sequential(nn.Linear(in_dim, h), nn.GELU(), nn.Linear(h, 1))
            nn.init.zeros_(self.combiner[-1].weight)                    # residual starts == OLS
            nn.init.zeros_(self.combiner[-1].bias)
        else:
            raise ValueError(
                f"combine_mode must be formula/router/mlp/moe/mlpcefr/mlpcefrx, got {combine_mode!r}")

    def _combine(self, dims, readout=None):  # dims (B, 4) -> cefr (B,)
        if self.combine_mode == "formula":
            return dims @ self.formula_coef + self.formula_intercept
        if self.combine_mode == "router":
            return self.combiner(dims).squeeze(-1)
        base = dims @ self.formula_coef + self.formula_intercept          # OLS residual base
        if self.combine_mode == "mlp":
            return base + self.combiner(dims).squeeze(-1)
        if self.combine_mode == "moe":
            # moe: OLS + gated mixture of expert corrections
            w = torch.softmax(self.moe_gate(dims), dim=-1)               # (B, K)
            ex = torch.cat([e(dims) for e in self.moe_experts], dim=-1)  # (B, K)
            return base + (w * ex).sum(-1)
        # mlpcefr / mlpcefrx: residual over OLS on DETACHED dims + OLS level (+ rich readout).
        # Fully detached -> CEFR loss updates only the combiner; dims train from their own loss.
        d = dims.detach()
        ols = d @ self.formula_coef + self.formula_intercept             # (B,)
        parts = [d, ols.unsqueeze(-1)]
        if self.combine_mode == "mlpcefrx":
            r = readout.detach().to(self.combiner[0].weight.dtype)
            parts.append(self.readout_proj(r))
        inp = torch.cat(parts, dim=-1).to(self.combiner[0].weight.dtype)
        return ols + self.combiner(inp).squeeze(-1)

    @staticmethod
    def _masked_mean_std(frames, pad_mask):
        """frames (B,T,D), pad_mask (B,T) True=pad -> (B, 2D) = [masked mean || masked std]."""
        valid = (~pad_mask).unsqueeze(-1).to(frames.dtype)          # (B,T,1)
        n = valid.sum(dim=1).clamp(min=1.0)                         # (B,1)
        mean = (frames * valid).sum(dim=1) / n                      # (B,D)
        var = (((frames - mean.unsqueeze(1)) ** 2) * valid).sum(dim=1) / n
        std = var.clamp(min=1e-8).sqrt()                            # (B,D)
        return torch.cat([mean, std], dim=-1)                       # (B, 2D)

    def _readout(self, input_ids, attention_mask, input_features, clip_bounds, task_ids,
                 no_audio=None):
        """Run the acoustic prior + Qwen. Returns (prior, pooled_qwen_hidden, frames, frame_mask).
        frames/frame_mask are None unless the dim_arch needs the frame sequence.
        no_audio (B,) 1.0 marks text-only rows whose soft tokens are attention-masked out."""
        tok_embeds = self._embed_tokens(input_ids)
        attn = attention_mask
        input_features = self._apply_spec_augment(input_features, clip_bounds)
        need_frames = self.dim_arch in (
            "routed_frames", "fused_frames", "bridge_frames", "gated_frames", "shared_pool",
            "mcasa")
        if need_frames:
            prior, frames, frame_mask = self.acoustic(
                input_features, clip_bounds, task_ids, return_frames=True)
        else:
            prior = self.acoustic(input_features, clip_bounds, task_ids)      # (B, Dw)
            frames = frame_mask = None

        bridge_in = prior
        if self.dim_arch == "bridge_frames":
            pooled_frames = self._masked_mean_std(frames, frame_mask)        # (B, 2Dw)
            fused = self.bridge_fusion(torch.cat(
                [prior, pooled_frames], dim=-1).to(self.bridge_fusion.weight.dtype))
            bridge_in = fused.to(prior.dtype)                                # (B, Dw), init == prior
        elif self.dim_arch == "shared_pool":
            # CLS discarded: the bridge reads the same frame mean+std pool as the acoustic head
            pooled_frames = self._masked_mean_std(frames, frame_mask)        # (B, 2Dw)
            bridge_in = self.bridge_pool_proj(
                pooled_frames.to(self.bridge_pool_proj.weight.dtype)).to(prior.dtype)

        if self.use_soft_tokens:
            projector_input = self.projector_input_norm(bridge_in)
            projected = self.projector[1](self.projector[0](projector_input))
            projected = self.projector_dropout(projected)
            soft = self.projector[2](projected).view(prior.size(0), self.n_soft, self.d_qwen)
            soft = self.projector_output_norm(soft)
            if self.acoustic_gate_logit is not None:
                soft = soft * self.acoustic_gate_logit.sigmoid()
            soft = soft.to(tok_embeds.dtype)
            soft_mask = torch.ones(attn.size(0), self.n_soft, dtype=attn.dtype, device=attn.device)
            if no_audio is not None:
                # text-only rows: mask the soft tokens out -> pure text prompt for Qwen; the
                # masked positions reach no attended output, so those rows also send zero
                # gradient into the projector/acoustic stack (source-separated training)
                keep = 1 - no_audio.to(device=soft_mask.device, dtype=soft_mask.dtype)
                soft_mask = soft_mask * keep.unsqueeze(1)
            inputs_embeds = torch.cat([soft, tok_embeds], dim=1)
            attn = torch.cat([soft_mask, attn], dim=1)
        else:                                   # m-CASA: Qwen sees the text prompt only
            inputs_embeds = tok_embeds

        position_ids = (attn.long().cumsum(dim=1) - 1).clamp(min=0)
        out = self.qwen(inputs_embeds=inputs_embeds, attention_mask=attn,
                        position_ids=position_ids, output_hidden_states=True, use_cache=False)
        last_hidden = out.hidden_states[-1]
        last_idx = (attn.size(1) - 1) - attn.flip(1).float().argmax(dim=1)
        pooled = last_hidden[torch.arange(last_hidden.size(0), device=last_hidden.device), last_idx]
        return prior, pooled, frames, frame_mask

    def _predict_dims(self, prior, pooled, frames=None, frame_mask=None):
        aux_prior = prior.detach() if self.detach_aux_acoustic else prior
        if self.dim_arch == "mcasa":
            hw = self.mcasa_flu_head.weight.dtype
            pooled_frames = self._masked_mean_std(frames, frame_mask).to(hw)      # (B, 2Dw)
            fr = frames.to(self.dim_query.dtype)                                  # attention in fp32
            query = self.dim_query.unsqueeze(0).expand(fr.size(0), -1, -1)        # (B, 2, Dw)
            # frame_mask True = pad -> key_padding_mask ignores those positions (same convention)
            pooled_dims, _ = self.dim_pool_attn(query, fr, fr, key_padding_mask=frame_mask,
                                                need_weights=False)               # (B, 2, Dw)
            ctx = pooled.detach().to(hw)                                          # detached content
            flu = self.mcasa_flu_head(
                torch.cat([pooled_dims[:, 0].to(hw), pooled_frames, ctx], dim=-1))     # (B, 1)
            pron = self.mcasa_pron_head(
                torch.cat([pooled_dims[:, 1].to(hw), pooled_frames, ctx], dim=-1))     # (B, 1)
            content = self.content_head(pooled.to(self.content_head.weight.dtype))     # (B, 2)
            return torch.cat([flu.float(), pron.float(), content.float()], dim=1)      # (B, 4)
        if self.dim_arch == "routed":
            acoustic = self.aux_head(aux_prior.to(self.aux_head.weight.dtype))       # (B,2)
            content = self.content_head(pooled.to(self.content_head.weight.dtype))   # (B,2)
            return torch.cat([acoustic.float(), content.float()], dim=1)             # (B,4)
        if self.dim_arch in ("routed_frames", "fused_frames", "bridge_frames", "shared_pool"):
            pooled_frames = self._masked_mean_std(frames, frame_mask)               # (B, 2Dw)
            hw = self.acoustic_frame_head.weight.dtype
            if self.dim_arch in ("fused_frames", "shared_pool"):
                # + detached content context: the rubric judges fluency/pron by intelligibility
                # of the content; detach so this loss never backprops through the LLM
                acoustic_in = torch.cat(
                    [pooled_frames.to(hw), pooled.detach().to(hw)], dim=-1)         # (B, 2Dw+Dq)
            else:
                acoustic_in = pooled_frames.to(hw)
            acoustic = self.acoustic_frame_head(acoustic_in)                        # (B,2) [flu,pron]
            content = self.content_head(pooled.to(self.content_head.weight.dtype))  # (B,2) [range,acc]
            return torch.cat([acoustic.float(), content.float()], dim=1)            # (B,4)
        # gated / gated_frames (acoustic side: CLS prior vs frame mean+std pool)
        side = aux_prior if self.dim_arch == "gated" else self._masked_mean_std(frames, frame_mask)
        a = self.dim_head_acoustic(side.to(self.dim_head_acoustic.weight.dtype)).float()  # (B,4)
        f = self.dim_head_fused(pooled.to(self.dim_head_fused.weight.dtype)).float()           # (B,4)
        g = torch.sigmoid(self.dim_gate_logit).to(a.dtype)                                     # (4,)
        return g * a + (1.0 - g) * f                                                          # (B,4)

    def forward(self, input_ids, attention_mask, input_features=None, clip_bounds=None,
                task_ids=None, scores=None, dim_targets=None, dim_mask=None, cefr_mask=None,
                dim_tol=None, dim_censor=None, dim_censor_lo=None, dim_weights=None, no_audio=None,
                loss_weights=None, **unused):
        prior, pooled, frames, frame_mask = self._readout(
            input_ids, attention_mask, input_features, clip_bounds, task_ids, no_audio=no_audio)
        all_dims = self._predict_dims(prior, pooled, frames, frame_mask)   # (B, 4)
        # v2 combiner (mlpcefrx) also sees the rich detached readout (Qwen pooled + acoustic prior)
        readout = torch.cat([pooled, prior], dim=-1) if self.combine_mode == "mlpcefrx" else None
        cefr = self._combine(all_dims, readout)                     # (B,)
        out_logits = torch.cat([cefr.unsqueeze(1), all_dims], dim=1)  # (B, 5)

        loss = None
        if scores is not None:
            loss = cefr.new_zeros(())
            if dim_targets is not None and self.dim_loss_weight > 0:
                dt = dim_targets.to(all_dims.device).float()
                resid = (all_dims - dt).abs()                       # (B, 4)
                if dim_tol is not None:            # dead-zone: free within +/- per-row tol
                    tol = dim_tol.to(resid.device).float().unsqueeze(1)   # (B, 1)
                    resid = (resid - tol).clamp(min=0.0)
                if dim_censor is not None:
                    # RIGHT-CENSORED supervision (per-row threshold; 0 = off). For an element whose
                    # TARGET lies above the threshold, any prediction at or above the threshold is
                    # accepted as correct and only under-prediction is penalised — distance to the
                    # THRESHOLD, not to the target, so the loss is continuous at the boundary.
                    #
                    # Why: DigiTala carries 293/1712 train rows above B1 on fluency/pron while the
                    # DTA test range stops at ~3.1 (4 rows above 3.0). Forcing exact regression onto
                    # 4.0-4.9 labels spends capacity above the evaluated range and drags the whole
                    # prediction scale up — measured directly when the full DigiTala set was added:
                    # the prediction floor rose 1.18 -> 1.41 and <A1 bias worsened to +1.103.
                    # Censoring keeps the "this speaker is high" signal without the exact high value.
                    # Replaces (does not stack with) the symmetric tol on those elements: the hinge
                    # already grants an unbounded free region above the cut.
                    cut = dim_censor.to(resid.device).float().unsqueeze(1)         # (B, 1)
                    hi = (cut > 0) & (dt > cut)                                    # (B, 4)
                    resid = torch.where(hi, (cut - all_dims).clamp(min=0.0), resid)
                if dim_censor_lo is not None:
                    # LEFT-CENSORED counterpart. Plain MSE makes hedging toward the centre the safe
                    # play: against a true 0.5, predicting 1.25 costs 0.56, while predicting 0.75
                    # against a true 2.0 costs 1.56 -- so the expected-loss-minimising floor sits
                    # well above the real one. Below the cut we stop asking HOW far down, only that
                    # the model goes down: target under the cut + prediction under the cut = free,
                    # and over-prediction is charged its distance to the cut (continuous there).
                    #
                    # Only meaningful with tail DATA to act on: real train had 4 rows <=1.25 on
                    # fluency; with the TTS low tier it is 444.
                    lo = dim_censor_lo.to(resid.device).float().unsqueeze(1)       # (B, 1)
                    below = (lo > 0) & (dt < lo)                                   # (B, 4)
                    resid = torch.where(below, (all_dims - lo).clamp(min=0.0), resid)
                sq = resid.square()                                 # (B, 4)
                if dim_mask is not None:
                    m = dim_mask.to(sq.device).float()             # (B, 4) 0/1 supervision
                    if dim_weights is not None:
                        # CEFR-BALANCED ("macro") dim loss. Plain MSE is minimised by hedging toward
                        # the conditional mean, and with 62% of the fluency labels at A2 that pull is
                        # strong: predictions collapse to a narrow band around the centre. Weighting
                        # each row by the inverse frequency of ITS OWN band re-prices the tails so a
                        # rare low row counts as much as a common middle one.
                        #
                        # Precedent in this project: the v2 study's 0.5-step CEFR-balanced loss took
                        # pred sd 0.18 -> 0.33 and improved BOTH RMSE (0.751 -> 0.607) and Spearman
                        # (0.268 -> 0.343). Unlike censoring the tails, this keeps a gradient
                        # everywhere, so within-tail ordering is still learnable.
                        #
                        # Weights fold into the mask, so a masked element still contributes nothing
                        # and the denominator stays the weighted count of SUPERVISED elements.
                        m = m * dim_weights.to(sq.device).float()
                    per_dim = (sq * m).sum(0) / m.sum(0).clamp(min=1e-6)  # mean over supervised rows
                    dim_loss = per_dim.mean()                      # equal weight per dim
                else:
                    dim_loss = sq.mean()
                loss = loss + self.dim_loss_weight * dim_loss
                if self.dim_spread_weight > 0:
                    # VARIANCE MATCHING. Attacks compression directly instead of sideways. Any loss
                    # whose minimiser is a conditional statistic (mean for MSE, quantile for pinball)
                    # shrinks toward the MARGINAL statistic as uncertainty grows, so switching to
                    # quantile/expectile does not cure shrinkage -- it only moves the target
                    # statistic. Reweighting (--dim_balanced_loss) re-prices the tails; this instead
                    # penalises the symptom we actually measure: pred sd / true sd was 0.49 at
                    # baseline against 1.0 for a calibrated model.
                    #
                    # Per dim, over SUPERVISED elements in the batch only. Batch-level statistic, so
                    # it is noisy at small batch sizes -- keep the weight small (~0.05-0.2) and treat
                    # it as a regulariser, not an objective.
                    mm = dim_mask.to(all_dims.device).float() if dim_mask is not None \
                        else torch.ones_like(all_dims)
                    n = mm.sum(0).clamp(min=2.0)
                    # zero the masked entries FIRST: 0 * NaN = NaN would survive otherwise
                    ap_ = torch.where(mm > 0, all_dims, torch.zeros_like(all_dims))
                    at_ = torch.where(mm > 0, dt, torch.zeros_like(dt))
                    mu_p = ap_.sum(0) / n
                    mu_t = at_.sum(0) / n
                    sd_p = ((((ap_ - mu_p) ** 2) * mm).sum(0) / n + 1e-4).sqrt()
                    sd_t = ((((at_ - mu_t) ** 2) * mm).sum(0) / n + 1e-4).sqrt()
                    enough = (mm.sum(0) >= 2.0).float()      # need >=2 supervised rows for an sd
                    spread = (((sd_p - sd_t) ** 2) * enough).sum() / enough.sum().clamp(min=1.0)
                    loss = loss + self.dim_spread_weight * spread
            if self.cefr_loss_weight > 0:
                tgt = scores.to(cefr.device).float()
                csq = (cefr - tgt).square()                        # (B,)
                cm = (cefr_mask.to(csq.device).float() if cefr_mask is not None
                      else torch.ones_like(csq))
                if loss_weights is not None:      # same band weighting on the CEFR guide term
                    cm = cm * loss_weights.to(csq.device).float()
                if cefr_mask is not None or loss_weights is not None:
                    cefr_loss = (csq * cm).sum() / cm.sum().clamp(min=1e-6)
                else:
                    cefr_loss = csq.mean()
                loss = loss + self.cefr_loss_weight * cefr_loss
            if self.dim_loss_weight <= 0 and self.cefr_loss_weight <= 0:
                tgt = scores.to(cefr.device).float()   # safety: never a zero objective
                loss = (cefr - tgt).square().mean()

        return {"loss": loss, "logits": out_logits} if loss is not None else {"logits": out_logits}
