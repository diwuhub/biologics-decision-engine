"""Context-of-use taxonomy for NAM readiness benchmarks."""

CONTEXT_OF_USE_TAXONOMY = {
    "safety_pharmacology": {
        "label": "General safety pharmacology",
        "maturity": 0.80,
    },
    "hepatotoxicity_screening": {
        "label": "Hepatotoxicity screening",
        "maturity": 0.90,
    },
    "nephrotoxicity_screening": {
        "label": "Nephrotoxicity screening",
        "maturity": 0.75,
    },
    "cardiotoxicity_screening": {
        "label": "Cardiotoxicity / proarrhythmia screening",
        "maturity": 0.95,
    },
    "immunogenicity_prediction": {
        "label": "Immunogenicity prediction",
        "maturity": 0.70,
    },
    "viral_safety": {
        "label": "Viral safety / viral clearance prediction",
        "maturity": 0.45,
    },
    "bioequivalence": {
        "label": "Bioequivalence / biowaiver support",
        "maturity": 0.95,
    },
}
