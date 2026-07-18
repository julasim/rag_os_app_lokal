"""Docling-Konvertierung: Datei -> DoclingDocument, mit der C0-validierten
Offline-Konfiguration.

Die genaue Beschwoerung stammt aus dem C0-Spike (Masterplan Track C):
  - `PdfPipelineOptions.artifacts_path` auf den vorab gezogenen Modell-Cache,
  - `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` via config.apply_offline_env(),
  - `do_ocr=False` fuer born-digital PDFs (kein RapidOCR-Runtime-Download).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .config import IngestConfig


@lru_cache(maxsize=4)
def _converter(ocr: bool, artifacts_path: str | None):
    """Baut (und cached) einen DocumentConverter fuer eine Options-Kombination."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pdf_opts = PdfPipelineOptions()
    pdf_opts.do_ocr = ocr
    if artifacts_path:
        pdf_opts.artifacts_path = artifacts_path

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)}
    )


def convert(path: str | Path, config: IngestConfig):
    """Parst die Datei und gibt das DoclingDocument zurueck."""
    config.apply_offline_env()
    conv = _converter(ocr=(config.ocr != "off"), artifacts_path=config.artifacts_path)
    result = conv.convert(str(path))
    return result.document
