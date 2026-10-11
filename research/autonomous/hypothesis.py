V08_HYPOTHESIS = (
    "2024 연간 누적 수익 하나만으로 shortlist를 고르는 대신 chronological quarters에서 "
    "반복적으로 양수인 genome을 선택하면 2025 development generalization이 개선될 수 있다."
)


def hypothesis():
    return {"hypothesis": V08_HYPOTHESIS, "changedVariables": ["shortlist_selection"],
            "unchangedVariables": ["training_population", "genome", "features", "fitness", "mutation",
                                   "crossover", "execution", "costs", "symbols", "2025_period"],
            "expectedEffect": "Reduce selection of genomes whose 2024 annual gain comes from one block",
            "riskOfOverfit": "2025 has been repeatedly observed; DEVELOPMENT ONLY, not independent OOS",
            "reason": "Temporal consistency is a structural selection hypothesis; no 2025 threshold fitting"}
