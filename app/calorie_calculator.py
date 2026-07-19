ACTIVITY_FACTORS = {
    "low": 1.2,
    "medium": 1.45,
    "high": 1.7,
}

GOAL_FACTORS = {
    "lose": 0.85,
    "maintain": 1.0,
    "gain": 1.1,
}

PROTEIN_PER_KG = {
    "lose": 1.8,
    "maintain": 1.5,
    "gain": 1.8,
}


def calculate_targets(
    sex: str,
    age: int,
    height: int,
    weight: float,
    goal: str,
    activity: str,
) -> tuple[int, int]:
    if sex == "male":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    calorie_target = bmr * ACTIVITY_FACTORS[activity] * GOAL_FACTORS[goal]
    protein_target = weight * PROTEIN_PER_KG[goal]
    return round(calorie_target), round(protein_target)


def calculate_water_target(weight: float) -> int:
    target = round((weight * 30) / 50) * 50
    return max(1500, min(3000, target))
