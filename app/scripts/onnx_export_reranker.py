"""ONNX-Export + INT8-Quantisierung des Rerankers (bge-reranker-v2-m3).

Läuft NUR in der Docker-Wegwerf-Stage `reranker-build` (siehe app/Dockerfile),
wo torch + optimum installiert sind. Ergebnis-Dateien (model.onnx,
model_quantized.onnx, Tokenizer) werden per `COPY --from` ins schlanke
Serving-Image übernommen — torch bleibt draußen.

Ranking-Äquivalenz: bge-reranker gibt ein Logit je Paar; INT8-Rundung ändert
absolute Scores minimal, die Reihenfolge praktisch nicht (in M1.3 gegen
sentence-transformers per Rang-Korrelation verifiziert).
"""
from __future__ import annotations

import os
import sys

from optimum.onnxruntime import ORTModelForSequenceClassification, ORTQuantizer
from optimum.onnxruntime.configuration import AutoQuantizationConfig
from transformers import AutoTokenizer

MODEL = "BAAI/bge-reranker-v2-m3"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/export/reranker"


def main() -> None:
    # fp32-ONNX-Export (lädt das HF-Modell — Build-Zeit-Download).
    model = ORTModelForSequenceClassification.from_pretrained(MODEL, export=True)
    model.save_pretrained(OUT)
    AutoTokenizer.from_pretrained(MODEL).save_pretrained(OUT)

    # Dynamische INT8-Quantisierung — KEINE Kalibrierdaten nötig (is_static=False).
    # avx2 = breit lauffähig (dyn. quantisierte Modelle laufen ISA-unabhängig korrekt,
    # avx2 nur als Kernel-Präferenz — kein AVX512-Zwang auf unbekannter VPS-CPU).
    quantizer = ORTQuantizer.from_pretrained(OUT)
    qconfig = AutoQuantizationConfig.avx2(is_static=False, per_channel=True)
    quantizer.quantize(save_dir=OUT, quantization_config=qconfig)

    # fp32-Export entfernen — das Serving-Image braucht nur das INT8-Modell
    # (reranker.py bevorzugt model_quantized.onnx). Das fp32-Gewicht liegt als
    # externe Daten (`model.onnx_data`, ~2,3 GB) daneben → sonst bläht es das
    # Image massiv auf. NACH der Quantisierung löschen.
    for stale in ("model.onnx", "model.onnx_data"):
        p = os.path.join(OUT, stale)
        if os.path.exists(p):
            os.remove(p)
            print(f"  fp32-Rest entfernt: {stale}")
    print(f"reranker ONNX exportiert + INT8-quantisiert (nur INT8 behalten) -> {OUT}")


if __name__ == "__main__":
    main()
