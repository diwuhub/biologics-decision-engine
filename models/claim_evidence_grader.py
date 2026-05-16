"""
Claim-Evidence Grader — Grade evidence strength of scientific claims.

Rubric:
  - strong: Multiple independent validations, regulatory acceptance, causal mechanism
  - moderate: Published data, plausible mechanism, limited validation
  - weak: Single study, correlative only, no independent validation
  - anecdotal: Opinion, press release, social media, no data

Uses TF-IDF + LogisticRegression baseline.

Usage:
    python -m models.claim_evidence_grader --evaluate
    python -m models.claim_evidence_grader --predict "Phase 3 trial showed..."
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score


TRAINING_DATA = [
    # strong
    ("Phase 3 randomized controlled trial with 500 patients demonstrated statistically significant improvement", "strong"),
    ("Multiple independent validation studies across 3 institutions confirmed the finding with p<0.001", "strong"),
    ("FDA-approved companion diagnostic based on this biomarker with validated sensitivity and specificity", "strong"),
    ("Mechanistic study with knockout model proved causal relationship between target and disease phenotype", "strong"),
    ("Published meta-analysis of 12 clinical trials confirmed the efficacy endpoint with narrow confidence intervals", "strong"),
    ("Validated biomarker qualified by FDA DDT program with prospective clinical utility demonstrated", "strong"),
    ("Genome-wide association study with 50000 subjects identified causal variant with functional validation", "strong"),
    ("Peer-reviewed publication in NEJM with independent replication in separate cohort", "strong"),
    # moderate
    ("Published Phase 2 study in 80 patients showed promising response rate above historical control", "moderate"),
    ("Preclinical efficacy demonstrated in two animal models with consistent dose-response relationship", "moderate"),
    ("Retrospective analysis of clinical database showed significant correlation between marker and outcome", "moderate"),
    ("In vitro binding assay and cell-based functional assay support the proposed mechanism of action", "moderate"),
    ("Conference presentation at AACR showing preliminary data from 30-patient cohort", "moderate"),
    ("Single-center prospective study with 100 patients but no independent validation yet", "moderate"),
    ("Published case series of 15 patients with consistent response to treatment", "moderate"),
    # weak
    ("Single pilot study in 10 patients with no control arm showed trend toward improvement", "weak"),
    ("In vitro only data from cell lines without any in vivo or clinical confirmation", "weak"),
    ("Poster presentation at regional meeting with preliminary data from 5 samples", "weak"),
    ("Computational prediction without any experimental validation of the binding hypothesis", "weak"),
    ("Observational study with significant confounding factors and no adjustment", "weak"),
    ("Preclinical data in a single animal model with uncertain human translatability", "weak"),
    # anecdotal
    ("Company press release announcing positive top-line results without detailed data", "anecdotal"),
    ("Social media post by KOL about personal experience with off-label use", "anecdotal"),
    ("WeChat article discussing rumored clinical trial results from unnamed source", "anecdotal"),
    ("Investor presentation slide showing cartoon mechanism without supporting data", "anecdotal"),
    ("Blog post by industry analyst speculating on potential clinical applications", "anecdotal"),
    ("Expert opinion in editorial without citing specific data or studies", "anecdotal"),
    ("Patent filing describing theoretical application without proof of concept", "anecdotal"),
    ("Preprint server manuscript with no peer review and preliminary in silico results", "anecdotal"),

    # --- Expanded training data below ---

    # strong (additional)
    ("Pivotal Phase 3 trial with 1200 subjects met primary and all secondary endpoints with pre-specified statistical significance", "strong"),
    ("Systematic review and meta-analysis of 25 randomized trials published in Lancet confirmed treatment benefit", "strong"),
    ("FDA-qualified drug development tool based on prospective validation in 3 independent clinical cohorts", "strong"),
    ("Validated companion diagnostic with 95% sensitivity and 92% specificity approved alongside therapeutic indication", "strong"),
    ("Large-scale genome-wide CRISPR screen independently replicated in two labs confirmed essential gene targets", "strong"),
    ("Prospective multi-center registry with 10000 patients demonstrated long-term safety and durable efficacy over 5 years", "strong"),
    ("Double-blind placebo-controlled crossover study confirmed dose-dependent pharmacodynamic effect in healthy volunteers", "strong"),
    ("International collaborative study across 8 sites validated the analytical method with inter-lab reproducibility CV below 5%", "strong"),
    ("FDA Breakthrough Therapy designation granted based on Phase 2 data with confirmatory Phase 3 meeting all endpoints", "strong"),
    ("EMA positive CHMP opinion based on two adequate and well-controlled trials demonstrating superiority over active comparator", "strong"),
    ("Bayesian adaptive platform trial with 2000 patients demonstrated posterior probability of superiority exceeding 99%", "strong"),
    ("Independently validated predictive biomarker included in NCCN guidelines as standard of care diagnostic", "strong"),
    ("Causal relationship established through Mendelian randomization study with 100000 subjects and functional genomic validation", "strong"),
    ("Phase 3 non-inferiority trial met pre-specified margin with consistent results across all pre-defined subgroups", "strong"),
    ("Real-world evidence study using FDA Sentinel system with 500000 patient records confirmed post-market safety signal", "strong"),
    ("Multi-omic integration study with proteogenomic validation published in Nature Medicine with independent external validation cohort", "strong"),
    ("WHO-prequalified assay validated across 15 national reference laboratories with standardized performance criteria", "strong"),
    ("Long-term follow-up data from registrational trial demonstrated sustained complete response at 5-year landmark analysis", "strong"),
    ("Prospective biomarker-stratified trial confirmed predictive utility with pre-specified interaction test reaching significance", "strong"),
    ("Reproducible structure-activity relationship established across 500 analogs with crystal structure-confirmed binding mode", "strong"),

    # moderate (additional)
    ("Phase 2a dose-finding study in 60 patients identified optimal dose with biomarker response correlation", "moderate"),
    ("Retrospective matched cohort analysis from electronic health records showed hazard ratio of 0.65 for primary outcome", "moderate"),
    ("Two independent preclinical xenograft models showed tumor growth inhibition exceeding 60% at clinically relevant doses", "moderate"),
    ("Poster presentation at ASCO with updated data from expansion cohort of 45 patients showing durable responses", "moderate"),
    ("Published case-control study with 200 subjects identified significant association between exposure and outcome", "moderate"),
    ("In vitro potency assay showing dose-response with IC50 in nanomolar range supported by cell-based functional readout", "moderate"),
    ("Pharmacokinetic modeling and simulation predicted human exposure within therapeutic window based on allometric scaling", "moderate"),
    ("Single-arm Phase 2 trial in rare disease with 25 patients exceeded pre-specified efficacy threshold", "moderate"),
    ("Ex vivo patient-derived organoid screening showed sensitivity correlation with clinical response in pilot study", "moderate"),
    ("Published analysis of FDA Adverse Event Reporting System database identified statistically significant disproportionality signal", "moderate"),
    ("Multi-species toxicology study with NOAEL supporting 10-fold safety margin at projected human therapeutic dose", "moderate"),
    ("Peer-reviewed mechanistic study demonstrating target engagement using validated PET tracer in non-human primates", "moderate"),
    ("Conference abstract at AACR reporting biomarker-positive subgroup analysis from Phase 1b expansion cohort", "moderate"),
    ("Systematic literature review of 30 publications supporting the biological rationale for the therapeutic approach", "moderate"),
    ("Phase 1 first-in-human dose escalation completed with pharmacodynamic biomarker modulation observed at higher doses", "moderate"),
    ("Real-world data analysis from 3 academic centers showing consistent treatment patterns and outcomes across sites", "moderate"),
    ("Published translational research establishing mechanistic link between target and disease in patient-derived tissues", "moderate"),
    ("Non-human primate efficacy study with disease-relevant model showing statistically significant primary endpoint improvement", "moderate"),
    ("Platform clinical trial interim analysis showing signal of activity in one of four evaluated combinations", "moderate"),
    ("Validated in silico PBPK model accurately predicted clinical PK in Phase 1 within 2-fold of observed values", "moderate"),
    ("Pre-specified subgroup analysis from completed Phase 3 trial suggesting differential benefit in biomarker-defined population", "moderate"),

    # weak (additional)
    ("Single-arm proof-of-concept study with 8 patients and no formal statistical hypothesis testing", "weak"),
    ("In vitro cytotoxicity assay on two cell lines without any animal model or clinical correlation", "weak"),
    ("Computational docking study predicting binding affinity without experimental confirmation of the binding site", "weak"),
    ("Retrospective chart review of 12 patients at a single institution with no control group", "weak"),
    ("High-throughput screen hit with micromolar potency and no selectivity data against off-targets", "weak"),
    ("Unpublished internal data from a single GLP toxicology study showing equivocal results", "weak"),
    ("Exploratory post-hoc subgroup analysis from failed Phase 2 trial suggesting potential benefit in small subset", "weak"),
    ("Gene expression signature derived from public database without prospective clinical validation", "weak"),
    ("Animal model with questionable disease relevance showing modest effect at supratherapeutic doses only", "weak"),
    ("Cross-sectional survey data suggesting correlation without controlling for major confounders", "weak"),
    ("Single biomarker measurement at one time point without longitudinal data or established clinical cutoff", "weak"),
    ("Bioinformatic pathway analysis predicting target involvement without any wet-lab experimental confirmation", "weak"),
    ("Uncontrolled compassionate use data from 3 patients with incomplete follow-up documentation", "weak"),
    ("In vitro synergy study using Bliss independence model without in vivo combination data", "weak"),
    ("Published case report of single exceptional responder without mechanistic investigation", "weak"),
    ("Preliminary flow cytometry data from 5 patient samples suggesting immune cell population shifts", "weak"),
    ("Student thesis presenting unpublished pilot data with methodological limitations acknowledged by authors", "weak"),
    ("Preclinical pharmacology study using only intraperitoneal dosing route with no oral bioavailability assessment", "weak"),
    ("Abstract-only publication from regional symposium with limited experimental details provided", "weak"),
    ("Tissue microarray analysis of archived samples without matched clinical outcome data", "weak"),
    ("Molecular dynamics simulation predicting protein conformational change without mutagenesis validation", "weak"),
    ("Accelerated stability data extrapolation from 3-month timepoint predicting 24-month shelf life without supporting real-time data", "weak"),

    # anecdotal (additional)
    ("CEO interview on CNBC discussing expected Phase 3 readout without sharing any clinical data", "anecdotal"),
    ("LinkedIn post by former employee claiming promising internal pipeline results", "anecdotal"),
    ("Industry conference keynote presenting aspirational vision for the technology without supporting experiments", "anecdotal"),
    ("Sell-side analyst report projecting blockbuster sales based on mechanism of action alone", "anecdotal"),
    ("News article quoting unnamed sources about rumored FDA advisory committee deliberations", "anecdotal"),
    ("Patient testimonial on advocacy website describing personal treatment experience", "anecdotal"),
    ("Company annual report describing pipeline asset as best-in-class without comparative data", "anecdotal"),
    ("Twitter thread by academic researcher sharing preliminary unpublished observations", "anecdotal"),
    ("Venture capital pitch deck showing competitive landscape slide without citing data sources", "anecdotal"),
    ("Investor day presentation with mechanism of action animation but no efficacy data disclosed", "anecdotal"),
    ("Trade publication article describing potential of new technology based on expert interviews only", "anecdotal"),
    ("Provisional patent application describing theoretical therapeutic use without reduction to practice", "anecdotal"),
    ("Conference panel discussion where experts expressed optimism about approach based on general principles", "anecdotal"),
    ("Company IR website claiming differentiated safety profile without head-to-head clinical comparison", "anecdotal"),
    ("Reddit discussion thread among patients sharing anecdotal treatment experiences and side effects", "anecdotal"),
    ("White paper published by consulting firm projecting market adoption based on analogy to different therapeutic area", "anecdotal"),
    ("Pre-IND meeting minutes suggesting FDA was receptive but no data were reviewed at the meeting", "anecdotal"),
    ("KOL advisory board summary reporting physician enthusiasm without quantitative survey data", "anecdotal"),
    ("Magazine feature article profiling company founders and their vision for the therapy", "anecdotal"),
    ("Earnings call transcript with management commentary on pipeline progress without disclosing trial results", "anecdotal"),
    ("YouTube video lecture by professor discussing theoretical mechanisms relevant to the drug target", "anecdotal"),
    ("ClinicalTrials.gov posting of planned study endpoints without any results posted", "anecdotal"),
]


def train_model(data=None):
    if data is None:
        data = TRAINING_DATA
    texts = [t for t, _ in data]
    labels = [l for _, l in data]
    vectorizer = TfidfVectorizer(max_features=300, ngram_range=(1, 2), stop_words="english")
    X = vectorizer.fit_transform(texts)
    model = LogisticRegression(max_iter=1000, C=1.0, random_state=42)
    model.fit(X, labels)
    return model, vectorizer


def evaluate_model(data=None):
    if data is None:
        data = TRAINING_DATA
    texts = [t for t, _ in data]
    labels = [l for _, l in data]
    X_train_t, X_test_t, y_train, y_test = train_test_split(texts, labels, test_size=0.3, random_state=42, stratify=labels)
    vectorizer = TfidfVectorizer(max_features=300, ngram_range=(1, 2), stop_words="english")
    X_train = vectorizer.fit_transform(X_train_t)
    X_test = vectorizer.transform(X_test_t)
    model = LogisticRegression(max_iter=1000, C=1.0, random_state=42)
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    return {"accuracy": round(accuracy_score(y_test, y_pred), 4),
            "n_train": len(X_train_t), "n_test": len(X_test_t),
            "report": classification_report(y_test, y_pred, output_dict=True)}


def predict(text, model=None, vectorizer=None):
    if model is None:
        model, vectorizer = train_model()
    X = vectorizer.transform([text])
    pred = model.predict(X)[0]
    proba = model.predict_proba(X)[0]
    return {"prediction": pred, "probabilities": {c: round(float(p), 4) for c, p in zip(model.classes_, proba)}}


def main():
    parser = argparse.ArgumentParser(description="Claim-Evidence Grader")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--predict", help="Grade a claim text")
    args = parser.parse_args()

    if args.evaluate:
        result = evaluate_model()
        print(f"Claim-Evidence Grader Evaluation")
        print(f"  Accuracy: {result['accuracy']:.1%}")
        print(f"  Train: {result['n_train']}, Test: {result['n_test']}")
        for cls in ["strong", "moderate", "weak", "anecdotal"]:
            if cls in result["report"]:
                r = result["report"][cls]
                print(f"    {cls:12s}: precision={r['precision']:.2f} recall={r['recall']:.2f} f1={r['f1-score']:.2f}")
    elif args.predict:
        result = predict(args.predict)
        print(f"Grade: {result['prediction']}")
        for c, p in sorted(result["probabilities"].items(), key=lambda x: -x[1]):
            print(f"  {c}: {p:.3f}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
