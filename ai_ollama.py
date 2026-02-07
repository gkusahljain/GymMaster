import requests
import math

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL_NAME = "phi3"


def estimate_calories(age, gender, height_cm, weight_kg, goal):
    if not age or not height_cm or not weight_kg:
        return 2200

    w = float(weight_kg)
    h = float(height_cm)
    a = int(age)

    if (gender or "").lower().startswith("f"):
        bmr = 10 * w + 6.25 * h - 5 * a - 161
    else:
        bmr = 10 * w + 6.25 * h - 5 * a + 5

    tdee = bmr * 1.4

    if goal == "weight_loss":
        target = tdee - 300
    elif goal == "muscle_gain":
        target = tdee + 250
    else:
        target = tdee

    return int(max(1200, min(target, 4000)))


def build_prompt(member, metric):
    name = member.get("name", "Member")
    gender = member.get("gender")
    age = member.get("age")
    height_cm = member.get("height_cm")
    diet_pref = member.get("diet_preference")
    goal = member.get("goal") or "general_fitness"

    if metric and metric.get("weight_kg"):
        weight_kg = metric["weight_kg"]
        bmi = metric.get("bmi")
        hr = metric.get("resting_heart_rate")
    else:
        weight_kg = member.get("current_weight_kg")
        bmi = None
        hr = None

    target_cal = estimate_calories(age, gender, height_cm, weight_kg, goal)

    diet_text = (
        "Vegetarian only (Indian foods)"
        if diet_pref == "veg"
        else "Non-vegetarian allowed (eggs/chicken/fish, Indian foods)"
        if diet_pref == "non_veg"
        else "Mixed veg + non-veg (Indian foods)"
    )

    goal_text = {
        "weight_loss": "fat loss with muscle retention",
        "muscle_gain": "muscle gain and strength",
        "general_fitness": "overall fitness and stamina",
    }.get(goal, "overall fitness")

    prompt = f"""
You are a professional Indian gym trainer and certified nutritionist.

Generate a CLEAN, WELL-STRUCTURED ONE-WEEK FITNESS PLAN.

MEMBER DETAILS:
Name: {name}
Gender: {gender}
Age: {age}
Height: {height_cm} cm
Weight: {weight_kg} kg
Goal: {goal_text}
Diet Preference: {diet_text}
Target Calories: ~{target_cal} kcal/day
BMI: {bmi if bmi else "Not available"}
Resting Heart Rate: {hr if hr else "Not available"}

STRICT RULES:
- No long paragraphs
- Use headings and bullet points only
- Plain text only (no markdown, no emojis)

OUTPUT STRUCTURE:
OVERVIEW (2–3 lines)

WEEKLY WORKOUT PLAN (Day 1 to Day 7)
- Warm-up
- Main workout
- Cardio
- Cool-down

WEEKLY MEAL PLAN (~{target_cal} kcal/day)
- Breakfast
- Snack
- Lunch
- Snack
- Dinner

DAILY MACROS:
Protein: XX g
Carbs: XX g
Fats: XX g

Return ONLY the plan text.
"""
    return prompt, target_cal


def call_ollama(prompt: str) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


def generate_ai_plan_text(member, metric):
    prompt, target_cal = build_prompt(member, metric)
    plan_text = call_ollama(prompt)

    # Macro split: 30% protein, 50% carbs, 20% fats
    protein = int((target_cal * 0.30) / 4)
    carbs = int((target_cal * 0.50) / 4)
    fats = int((target_cal * 0.20) / 9)

    macros = {
        "protein": protein,
        "carbs": carbs,
        "fat": fats,
    }

    return target_cal, macros, plan_text
