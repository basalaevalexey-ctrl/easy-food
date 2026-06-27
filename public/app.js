const tabs = document.querySelectorAll("[data-tab]");
const screens = document.querySelectorAll("[data-screen]");
const toast = document.querySelector(".toast");
const addToggle = document.querySelector("[data-add-toggle]");
const addMenu = document.querySelector("[data-add-menu]");
const dateLabel = document.querySelector("[data-date-label]");
const datePicker = document.querySelector("[data-date-picker]");
const datePrev = document.querySelector("[data-date-prev]");
const dateNext = document.querySelector("[data-date-next]");
const profileName = document.querySelector("[data-profile-name]");
const profileGoalLabel = document.querySelector("[data-profile-goal-label]");
const profileWeight = document.querySelector("[data-profile-weight]");
const profileHeight = document.querySelector("[data-profile-height]");
const profileActivity = document.querySelector("[data-profile-activity]");
const goalCalories = document.querySelector("[data-goal-calories]");
const goalProtein = document.querySelector("[data-goal-protein]");
const onboarding = document.querySelector("[data-onboarding]");
const onboardingForm = document.querySelector("[data-onboarding-form]");
const onboardingName = document.querySelector("[data-onboarding-name]");
const onboardingSex = document.querySelector("[data-onboarding-sex]");
const onboardingWeight = document.querySelector("[data-onboarding-weight]");
const onboardingHeight = document.querySelector("[data-onboarding-height]");
const onboardingActivity = document.querySelector("[data-onboarding-activity]");
const onboardingGoal = document.querySelector("[data-onboarding-goal]");
let calorieGoal = 2100;
let profileGoal = "support";
let profileSex = "male";
const profileStoreKey = "nyammetr-profile";

const text = {
  today: "\u0421\u0435\u0433\u043e\u0434\u043d\u044f",
  yesterday: "\u0412\u0447\u0435\u0440\u0430",
  tomorrow: "\u0417\u0430\u0432\u0442\u0440\u0430",
  gram: "\u0433",
  kcal: "\u043a\u043a\u0430\u043b",
  textNext: "\u041e\u0442\u043a\u0440\u043e\u0435\u043c \u0432\u0432\u043e\u0434 \u0442\u0435\u043a\u0441\u0442\u043e\u043c \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u043c \u0448\u0430\u0433\u043e\u043c",
  photoNext: "\u041e\u0442\u043a\u0440\u043e\u0435\u043c \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0443 \u0444\u043e\u0442\u043e \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u043c \u0448\u0430\u0433\u043e\u043c",
  smaller: "\u041f\u043e\u0440\u0446\u0438\u044f \u0443\u043c\u0435\u043d\u044c\u0448\u0435\u043d\u0430",
  larger: "\u041f\u043e\u0440\u0446\u0438\u044f \u0443\u0432\u0435\u043b\u0438\u0447\u0435\u043d\u0430",
  editGrams: "\u0422\u0435\u043f\u0435\u0440\u044c \u0433\u0440\u0430\u043c\u043c\u044b \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u0443\u044e\u0442\u0441\u044f \u0432 \u0441\u0442\u0440\u043e\u043a\u0430\u0445",
  save: "\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c",
  saved: "\u0413\u0440\u0430\u043c\u043c\u044b \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b",
  deleted: "\u0443\u0434\u0430\u043b\u0435\u043d",
  gramsLabel: "\u0413\u0440\u0430\u043c\u043c\u044b",
};

const goalLabels = {
  support: "\u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0442\u044c \u0444\u043e\u0440\u043c\u0443",
  lose: "\u0441\u043d\u0438\u0437\u0438\u0442\u044c \u0432\u0435\u0441",
  gain: "\u043d\u0430\u0431\u0440\u0430\u0442\u044c \u043c\u0430\u0441\u0441\u0443",
};

const today = startOfDay(new Date());
let selectedDate = new Date(today);

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function toInputDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function fromInputDate(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function formatDiaryDate(date) {
  const diffDays = Math.round((startOfDay(date) - today) / 86400000);

  if (diffDays === 0) return text.today;
  if (diffDays === -1) return text.yesterday;
  if (diffDays === 1) return text.tomorrow;

  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
  }).format(date);
}

function updateDiaryDate(date) {
  selectedDate = startOfDay(date);
  dateLabel.textContent = formatDiaryDate(selectedDate);
  datePicker.value = toInputDate(selectedDate);
}

function shiftDiaryDate(days) {
  const nextDate = new Date(selectedDate);
  nextDate.setDate(nextDate.getDate() + days);
  updateDiaryDate(nextDate);
}

function showScreen(name) {
  screens.forEach((screen) => {
    screen.classList.toggle("active", screen.dataset.screen === name);
  });

  tabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.classList.remove("show");
  }, 1800);
}

function parseAmount(value) {
  const source = "value" in value ? `${value.value} ${value.dataset.unit || text.gram}` : value.textContent;
  const match = source.trim().match(/^(\d+(?:[.,]\d+)?)\s*(.*)$/);

  if (!match) {
    return { value: 0, unit: text.gram };
  }

  return {
    value: Number(match[1].replace(",", ".")),
    unit: match[2] || text.gram,
  };
}

function parseCalories(value) {
  return Number(value.replace(/[^\d]/g, "")) || 0;
}

function formatAmount(value, unit) {
  return `${Math.max(1, Math.round(value))} ${unit}`;
}

function formatCalories(value) {
  return `${Math.max(1, Math.round(value))} ${text.kcal}`;
}

function getAmountNode(row) {
  return row.querySelector("input") || row.querySelector("small");
}

function readDishAmount(row) {
  return parseAmount(getAmountNode(row));
}

function readDishCalories(row) {
  return parseCalories(row.querySelector("strong").textContent);
}

function getDishRows(card) {
  return [...card.querySelectorAll(".dish-list span")];
}

function getMealCalories(card) {
  return getDishRows(card).reduce((sum, row) => sum + readDishCalories(row), 0);
}

function updateMealCard(card) {
  card.querySelector(".meal-toggle strong").textContent = formatCalories(getMealCalories(card));
}

function updateTotalCalories() {
  const total = [...document.querySelectorAll(".meal-card")].reduce((sum, card) => sum + getMealCalories(card), 0);
  const percent = Math.min(100, Math.round((total / calorieGoal) * 100));

  document.querySelectorAll("[data-calories]").forEach((node) => {
    node.textContent = Math.round(total);
  });

  document.querySelectorAll(".calorie-card .progress-line span").forEach((bar) => {
    bar.style.width = `${percent}%`;
  });
}

function readProfile() {
  return {
    name: profileName.textContent.trim() || "Алексей",
    sex: profileSex,
    weight: Number(profileWeight.value) || 82,
    height: Number(profileHeight.value) || 182,
    activity: profileActivity.value,
    goal: profileGoal,
  };
}

function applyProfile(profile) {
  profileGoal = profile.goal || "support";
  profileSex = profile.sex || "male";
  profileName.textContent = profile.name || "Алексей";
  profileGoalLabel.textContent = goalLabels[profileGoal] || goalLabels.support;
  profileWeight.value = profile.weight || 82;
  profileHeight.value = profile.height || 182;
  profileActivity.value = profile.activity || "medium";
  calculateGoals();
}

function saveProfile() {
  localStorage.setItem(profileStoreKey, JSON.stringify(readProfile()));
}

function loadProfile() {
  const saved = localStorage.getItem(profileStoreKey);

  if (!saved) {
    onboarding.hidden = false;
    return;
  }

  try {
    applyProfile(JSON.parse(saved));
  } catch {
    onboarding.hidden = false;
  }
}

function calculateGoals() {
  const weight = Math.max(35, Number(profileWeight.value) || 82);
  const height = Math.max(120, Number(profileHeight.value) || 182);
  const activityFactors = {
    low: 1.25,
    medium: 1.45,
    high: 1.65,
  };
  const goalFactors = {
    lose: 0.88,
    support: 1,
    gain: 1.1,
  };
  const factor = activityFactors[profileActivity.value] || activityFactors.medium;
  const sexOffset = profileSex === "female" ? -161 : 5;
  const bmr = 10 * weight + 6.25 * height - 5 * 30 + sexOffset;

  calorieGoal = Math.round((bmr * factor * (goalFactors[profileGoal] || 1)) / 50) * 50;
  const proteinGoal = Math.round(weight * (profileGoal === "gain" ? 1.9 : 1.6));

  goalCalories.textContent = `${calorieGoal} ${text.kcal}`;
  goalProtein.textContent = `${proteinGoal} ${text.gram}`;

  document.querySelectorAll("[data-calorie-goal-label]").forEach((node) => {
    node.textContent = `/ ${calorieGoal} ${text.kcal}`;
  });

  updateTotalCalories();
}

function scaleMeal(card, factor) {
  getDishRows(card).forEach((row) => {
    const amountNode = getAmountNode(row);
    const amount = readDishAmount(row);
    const calories = readDishCalories(row);

    amountNode.textContent = formatAmount(amount.value * factor, amount.unit);
    row.querySelector("strong").textContent = formatCalories(calories * factor);
  });

  updateMealCard(card);
  updateTotalCalories();
}

function deleteMeal(card) {
  const mealName = card.querySelector(".meal-toggle b").textContent;
  card.remove();
  updateTotalCalories();
  showToast(`${mealName} ${text.deleted}`);
}

function enterDishEditMode(card) {
  card.classList.add("editing");

  getDishRows(card).forEach((row) => {
    if (row.querySelector("input")) return;

    const amountNode = row.querySelector("small");
    const amount = parseAmount(amountNode);
    const calories = readDishCalories(row);
    row.dataset.kcalPerUnit = String(calories / amount.value);

    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.step = "1";
    input.inputMode = "numeric";
    input.value = String(Math.round(amount.value));
    input.dataset.unit = amount.unit;
    input.setAttribute("aria-label", `${text.gramsLabel}: ${row.querySelector("b").textContent}`);

    const unit = document.createElement("small");
    unit.className = "amount-unit";
    unit.textContent = amount.unit;

    amountNode.replaceWith(input, unit);

    input.addEventListener("input", () => updateDishAmount(row, input.value));
    input.addEventListener("blur", () => {
      if (!input.value || Number(input.value) <= 0) {
        input.value = "1";
        updateDishAmount(row, input.value);
      }
    });
  });

  const firstInput = card.querySelector(".dish-list input");
  if (firstInput) {
    firstInput.focus();
    firstInput.select();
  }

  ensureSaveGramsButton(card);
  showToast(text.editGrams);
}

function ensureSaveGramsButton(card) {
  const actions = card.querySelector(".meal-actions");

  if (actions.querySelector(".save-grams")) return;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "save-grams";
  button.textContent = text.save;
  actions.append(button);
}

function saveDishEditMode(card) {
  getDishRows(card).forEach((row) => {
    const input = row.querySelector("input");
    const unitNode = row.querySelector(".amount-unit");

    if (!input || !unitNode) return;

    const amount = Math.max(1, Math.round(Number(input.value) || 1));
    const amountText = document.createElement("small");
    amountText.textContent = formatAmount(amount, unitNode.textContent);
    input.replaceWith(amountText);
    unitNode.remove();
  });

  card.classList.remove("editing");
  card.querySelector(".save-grams")?.remove();
  updateMealCard(card);
  updateTotalCalories();
  showToast(text.saved);
}

function updateDishAmount(row, value) {
  const nextAmount = Number(String(value).replace(",", "."));

  if (!Number.isFinite(nextAmount) || nextAmount <= 0) return;

  const kcalPerUnit = Number(row.dataset.kcalPerUnit) || 0;
  row.querySelector("strong").textContent = formatCalories(nextAmount * kcalPerUnit);
  updateMealCard(row.closest(".meal-card"));
  updateTotalCalories();
}

function closeAddMenu() {
  addMenu.classList.remove("open");
  addToggle.setAttribute("aria-expanded", "false");
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    showScreen(tab.dataset.tab);
    closeAddMenu();
  });
});

addToggle.addEventListener("click", () => {
  const isOpen = addMenu.classList.toggle("open");
  addToggle.setAttribute("aria-expanded", String(isOpen));
});

document.querySelector("[data-add-text]").addEventListener("click", () => {
  closeAddMenu();
  showToast(text.textNext);
});

document.querySelector("[data-add-photo]").addEventListener("click", () => {
  closeAddMenu();
  showToast(text.photoNext);
});

document.querySelector("[data-missions-add]").addEventListener("click", () => {
  showScreen("home");
  const isOpen = addMenu.classList.toggle("open", true);
  addToggle.setAttribute("aria-expanded", String(isOpen));
});

document.querySelectorAll("[data-soon-update]").forEach((node) => {
  const showSoon = () => showToast("Скоро обновление");

  node.addEventListener("click", showSoon);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      showSoon();
    }
  });
});

[profileWeight, profileHeight, profileActivity].forEach((control) => {
  control.addEventListener("input", calculateGoals);
  control.addEventListener("change", calculateGoals);
  control.addEventListener("change", saveProfile);
});

onboardingForm.addEventListener("submit", (event) => {
  event.preventDefault();
  applyProfile({
    name: onboardingName.value.trim() || "Алексей",
    sex: onboardingSex.value,
    weight: Number(onboardingWeight.value) || 82,
    height: Number(onboardingHeight.value) || 182,
    activity: onboardingActivity.value,
    goal: onboardingGoal.value,
  });
  saveProfile();
  onboarding.hidden = true;
  showToast("Профиль настроен");
});

datePrev.addEventListener("click", () => shiftDiaryDate(-1));
dateNext.addEventListener("click", () => shiftDiaryDate(1));

dateLabel.addEventListener("click", () => {
  if (typeof datePicker.showPicker === "function") {
    datePicker.showPicker();
    return;
  }

  datePicker.focus();
  datePicker.click();
});

datePicker.addEventListener("change", () => {
  if (datePicker.value) {
    updateDiaryDate(fromInputDate(datePicker.value));
  }
});

document.querySelectorAll(".meal-card").forEach((card) => {
  const toggle = card.querySelector(".meal-toggle");

  toggle.addEventListener("click", () => {
    const isOpen = card.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(isOpen));
  });
});

document.querySelector(".meal-list").addEventListener("click", (event) => {
  const button = event.target.closest(".meal-actions button");

  if (!button) return;

  const card = button.closest(".meal-card");
  const action = button.dataset.mealAction;

  if (button.classList.contains("save-grams")) {
    saveDishEditMode(card);
    return;
  }

  if (action === "smaller") {
    scaleMeal(card, 0.85);
    showToast(text.smaller);
    return;
  }

  if (action === "larger") {
    scaleMeal(card, 1.15);
    showToast(text.larger);
    return;
  }

  if (action === "grams") {
    enterDishEditMode(card);
    return;
  }

  if (action === "delete") {
    deleteMeal(card);
  }
});

updateDiaryDate(today);
document.querySelectorAll(".meal-card").forEach(updateMealCard);
loadProfile();
calculateGoals();
updateTotalCalories();
