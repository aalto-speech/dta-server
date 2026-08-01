"""M-CASA scorer: construct the architecture, load the checkpoint, run one forward pass.

The architecture is built with the same hyperparameters the launcher used
(scripts/train_v3_le40_mcasa_noac_eqcap2x_ttsfluall3_s2022.sh) and then EVERY weight is
overwritten from the checkpoint, which carries a complete state dict — Qwen base + LoRA,
Whisper base + LoRA, aggregator, M-CASA heads, and the frozen-OLS buffers. The load is
strict: a missing or unexpected key raises rather than silently serving a partly random
model, which is the failure mode that matters here (a randomly-initialised head still
returns plausible-looking CEFR numbers).

Base model weights are therefore irrelevant to the result; they are pulled only to
instantiate module shapes and to get the tokenizer and the mel feature extractor.
"""
import sys
from pathlib import Path

import torch
from safetensors.torch import load_file
from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modeling.qwen_multidim_scorer import QwenMultiDimScorer  # noqa: E402  (vendored, byte-identical)

from .config import (ACOUSTIC_PROJECTOR_DROPOUT, AGG_LAYERS, ATTN_IMPL, AUTOCAST_DTYPE,
                     DIM_ORDER, FRAME_POOL, MAX_CHUNKS, MAX_TEXT_LEN, N_SOFT_TOKENS, N_TASKS,
                     QWEN_DIR, QWEN_LORA_ALPHA, QWEN_LORA_R, SCORER_WEIGHTS, WHISPER_DIR,
                     WHISPER_LORA_R)

_DTYPES = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}


class MCasaScorer:
    def __init__(self, device: str = "cuda", autocast_dtype: str = AUTOCAST_DTYPE):
        self.device = device
        self.autocast_dtype = _DTYPES[autocast_dtype]
        self.tokenizer = AutoTokenizer.from_pretrained(str(QWEN_DIR))
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        acoustic_kwargs = dict(
            whisper_name=str(WHISPER_DIR), max_chunks=MAX_CHUNKS, frame_pool=FRAME_POOL,
            n_layers=AGG_LAYERS, pos_encoding="rope", lora=True, lora_r=WHISPER_LORA_R,
            attn_impl=ATTN_IMPL, grad_checkpoint=False, n_tasks=N_TASKS,
            cache_dir=None)
        model = QwenMultiDimScorer(
            qwen_name=str(QWEN_DIR), acoustic_kwargs=acoustic_kwargs,
            n_soft_tokens=N_SOFT_TOKENS, use_acoustic=True,
            acoustic_projector_dropout=ACOUSTIC_PROJECTOR_DROPOUT,
            attn_impl=ATTN_IMPL,
            qwen_lora_r=QWEN_LORA_R, qwen_lora_alpha=QWEN_LORA_ALPHA,
            grad_checkpoint=False,
            dim_arch="mcasa", combine_mode="formula",
            # placeholders: the real OLS arrives with the checkpoint buffers below
            formula_coef=[0.25] * 4, formula_intercept=0.0,
            dim_loss_weight=1.0, cefr_loss_weight=0.15,
            cache_dir=None)

        state = load_file(str(SCORER_WEIGHTS))
        missing, unexpected = model.load_state_dict(state, strict=False)
        # peft/HF can legitimately leave tied or non-persistent entries out of a saved state
        # dict; anything else means the architecture here does not match the trained one.
        hard_missing = [k for k in missing if not k.endswith("rotary_emb.inv_freq")]
        if hard_missing or unexpected:
            raise RuntimeError(
                f"checkpoint does not match this architecture — "
                f"{len(hard_missing)} missing (e.g. {hard_missing[:3]}), "
                f"{len(unexpected)} unexpected (e.g. {list(unexpected)[:3]})")

        model.eval().to(device)
        for p in model.parameters():
            p.requires_grad_(False)
        self.model = model

        self.ols_coef = model.formula_coef.detach().float().cpu().tolist()
        self.ols_intercept = float(model.formula_intercept.detach().float().cpu())

    def tokenize(self, prompt: str):
        enc = self.tokenizer(prompt, truncation=True, max_length=MAX_TEXT_LEN,
                             return_tensors="pt")
        return enc["input_ids"], enc["attention_mask"]

    @torch.inference_mode()
    def score(self, input_features, clip_bounds, model_task_id: int, prompt: str) -> dict:
        """One recording -> raw CEFR + the 4 raw dimension scores.

        Returns MODEL-SPACE values. Calibration is applied by the pipeline, not here, so this
        stays the exact quantity the checkpoint's test_overall_predictions.csv contains.
        """
        input_ids, attention_mask = self.tokenize(prompt)
        batch = dict(
            input_ids=input_ids.to(self.device),
            attention_mask=attention_mask.to(self.device),
            input_features=[input_features.to(self.device)],   # list[(C,80,3000)] per sample
            clip_bounds=[clip_bounds.to(self.device)],
            task_ids=torch.tensor([model_task_id], dtype=torch.long, device=self.device),
        )
        # bf16 autocast reproduces the training/eval numerics; fp32 shifts scores slightly.
        use_amp = self.device.startswith("cuda") and self.autocast_dtype != torch.float32
        with torch.autocast("cuda", dtype=self.autocast_dtype, enabled=use_amp):
            out = self.model(**batch)
        logits = out["logits"].float().cpu()[0]                # (5,) [cefr, flu, pron, range, acc]
        dims = {name: float(logits[1 + i]) for i, name in enumerate(DIM_ORDER)}
        return {"cefr_raw": float(logits[0]), "dims_raw": dims}
