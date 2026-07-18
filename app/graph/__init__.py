"""Wissensgraph (Track D) — deterministische Beziehungs-Schicht über den Chunks.

Geschichtet: L1 (deterministisch: Normverweise/Ablöse/Tags/Ordner), L2
(Ähnlichkeit: bge-m3-kNN + MinHash), L3 (lokaler-LLM-Entity-Layer, Phase 2).
Analyse/PPR auf **Konzept-Ebene** (document/norm/tag/folder/issuer), nicht auf
Chunk-Ebene. Reiner Code (networkx/numpy, BSD) — kein übernommener Fremdcode.
"""
