"""Export the context-check model (autocorrect layer 3) to ONNX + int8.

    HF_HUB_OFFLINE=1 HF_HOME=.models/hf .venv/bin/python engine/export_context.py
        distilroberta-base masked LM -> .models/context/
    ... engine/export_context.py --model electra --out .models/context_electra
        electra-small discriminator (comparison only)

The masked LM is wrapped so the graph returns log-probs at one position per row:
(input_ids, attention_mask, pos) -> logprobs [batch, subset]. Two size tricks:
- The output head is untied from the input embeddings (the int8 quantizer breaks a
  weight shared by Gather and MatMul) and keeps only a vocabulary subset: the 16384
  most common BPE tokens plus every token of every confusion-set word in four spellings
  (" word", "word", " Word", "Word"). The softmax runs over that subset, which holds
  nearly all of the probability mass. The ids are saved to subset.txt.
- Weights are int8 (dynamic quantization), about 95 MB on disk, kept in
  model.int8.onnx.data next to model.int8.onnx (lower peak memory on load).
"""
import argparse
import os

import onnx
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import AutoModelForMaskedLM, AutoTokenizer, ElectraForPreTraining

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = {"roberta": "distilbert/distilroberta-base", "electra": "google/electra-small-discriminator"}
COMMON = 16384


def confusion_words():
    out = set()
    with open(os.path.join(ROOT, "engine", "confusion_sets.txt"), encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                out.update(line.split())
    return sorted(out)


def subset_ids(tok):
    ids = set(range(COMMON))
    for w in confusion_words():
        for v in (" " + w, w, " " + w[0].upper() + w[1:], w[0].upper() + w[1:]):
            ids.update(tok.encode(v, add_special_tokens=False))
    return sorted(ids)


class MaskedAt(torch.nn.Module):
    def __init__(self, mlm, keep):
        super().__init__()
        self.roberta = mlm.roberta
        self.dense, self.norm = mlm.lm_head.dense, mlm.lm_head.layer_norm
        self.pad = mlm.config.pad_token_id
        idx = torch.tensor(keep)
        self.decoder = torch.nn.Linear(768, len(keep))
        self.decoder.weight = torch.nn.Parameter(mlm.lm_head.decoder.weight[idx].detach().clone())
        self.decoder.bias = torch.nn.Parameter(mlm.lm_head.decoder.bias[idx].detach().clone())

    def forward(self, input_ids, attention_mask, pos):
        # Explicit position and token-type ids: the traced defaults bake in the dummy length.
        position_ids = torch.cumsum(attention_mask, dim=1) * attention_mask + self.pad
        h = self.roberta(input_ids=input_ids, attention_mask=attention_mask, position_ids=position_ids,
                         token_type_ids=torch.zeros_like(input_ids)).last_hidden_state
        h = h[torch.arange(h.shape[0]), pos]
        h = self.norm(torch.nn.functional.gelu(self.dense(h)))
        return torch.log_softmax(self.decoder(h), dim=-1)


class ElectraOut(torch.nn.Module):
    def __init__(self, disc):
        super().__init__()
        self.disc = disc

    def forward(self, input_ids, attention_mask):
        pos = torch.arange(input_ids.shape[1], dtype=torch.long).unsqueeze(0).expand_as(input_ids)
        return self.disc(input_ids=input_ids, attention_mask=attention_mask, position_ids=pos,
                         token_type_ids=torch.zeros_like(input_ids)).logits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=sorted(MODELS), default="roberta")
    ap.add_argument("--out", default=os.path.join(ROOT, ".models", "context"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(MODELS[a.model])
    dummy = tok(["so it is better then the", "ok"], return_tensors="pt", padding=True)
    ids, att = dummy["input_ids"], dummy["attention_mask"]
    fp32 = os.path.join(a.out, "model.onnx")
    if a.model == "roberta":
        keep = subset_ids(tok)
        with open(os.path.join(a.out, "subset.txt"), "w") as f:
            f.write("\n".join(map(str, keep)) + "\n")
        model = MaskedAt(AutoModelForMaskedLM.from_pretrained(MODELS[a.model]).eval(), keep).eval()
        args = (ids, att, torch.tensor([3, 1]))
        names, outs = ["input_ids", "attention_mask", "pos"], ["logprobs"]
        axes = {"input_ids": {0: "b", 1: "s"}, "attention_mask": {0: "b", 1: "s"},
                "pos": {0: "b"}, "logprobs": {0: "b"}}
    else:
        model = ElectraOut(ElectraForPreTraining.from_pretrained(MODELS[a.model]).eval())
        args = (ids, att)
        names, outs = ["input_ids", "attention_mask"], ["logits"]
        axes = {n: {0: "b", 1: "s"} for n in names + outs}
    with torch.no_grad():
        torch.onnx.export(model, args, fp32, input_names=names, output_names=outs,
                          dynamic_axes=axes, opset_version=17, dynamo=False)
    int8 = os.path.join(a.out, "model.int8.onnx")
    quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8)
    os.remove(fp32)  # the runtime only loads the int8 file
    # Weights in a side file: onnxruntime then reads them straight into tensors instead of
    # holding a second copy inside the parsed graph. Peak RSS on load drops by ~110 MB.
    m = onnx.load(int8)
    for f in (int8, int8 + ".data"):
        if os.path.exists(f):
            os.remove(f)
    onnx.save_model(m, int8, save_as_external_data=True, all_tensors_to_one_file=True,
                    location="model.int8.onnx.data", size_threshold=1024)
    tok.save_pretrained(a.out)
    with open(os.path.join(a.out, "kind.txt"), "w") as f:
        f.write(a.model + "\n")
    for f in sorted(os.listdir(a.out)):
        print(f, round(os.path.getsize(os.path.join(a.out, f)) / 1e6, 1), "MB")


if __name__ == "__main__":
    main()
