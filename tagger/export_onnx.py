"""Export a trained run to ONNX + int8 dynamic quantization, with tokenizer files.

python tagger/export_onnx.py --run electra-small   # writes .models/tagger/
"""
import argparse
import os
import shutil

import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import AutoModelForTokenClassification, AutoTokenizer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ap = argparse.ArgumentParser()
ap.add_argument("--run", default="electra-small")
ap.add_argument("--out", default=ROOT + "/.models/tagger")
a = ap.parse_args()
src = f"{ROOT}/.models/runs/{a.run}"
os.makedirs(a.out, exist_ok=True)
model = AutoModelForTokenClassification.from_pretrained(src).eval()
tok = AutoTokenizer.from_pretrained(src)
names = ["input_ids", "attention_mask"]
dummy = tok(["um so we should meet", "ok"], is_split_into_words=False, return_tensors="pt", padding=True)
args = (dummy["input_ids"], dummy["attention_mask"])
fp32 = os.path.join(a.out, "model.onnx")
torch.onnx.export(model, args, fp32, input_names=names, output_names=["logits"],
                  dynamic_axes={n: {0: "b", 1: "s"} for n in names} | {"logits": {0: "b", 1: "s"}},
                  opset_version=17, dynamo=False)
int8 = os.path.join(a.out, "model.int8.onnx")
quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8)
tok.save_pretrained(a.out)
for f in os.listdir(a.out):
    if f.endswith(".onnx"):
        print(f, round(os.path.getsize(os.path.join(a.out, f)) / 1e6, 1), "MB")
