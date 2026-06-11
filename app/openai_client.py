import base64
import json
import logging

from openai import AsyncOpenAI, OpenAIError

from app.models import FoodEstimate

logger = logging.getLogger(__name__)


class OpenAIRecognitionError(Exception):
    pass


class NotFoodError(Exception):
    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(reason)


class FoodRecognitionClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key or "missing")
        self.model = model

    async def estimate_text(self, text: str) -> FoodEstimate:
        content = [
            {
                "type": "text",
                "text": (
                    "Определи, описывает ли пользователь еду или напиток. "
                    "Если это еда, оцени калории и БЖУ максимально аккуратно: учитывай количество, граммы, штуки, "
                    "тип приготовления, масло, соусы и типичные порции. Если масса не указана, используй обычную "
                    "домашнюю порцию и снизь confidence. "
                    "Если это не еда и не напиток, верни is_food=false и не выдумывай калории. "
                    "Верни только JSON без markdown.\n\n"
                    f"Сообщение пользователя: {text}"
                ),
            }
        ]
        return await self._estimate(content)

    async def estimate_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> FoodEstimate:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content = [
            {
                "type": "text",
                "text": (
                    "Определи, есть ли на фото еда или напиток. "
                    "Если это еда, оцени калории и БЖУ максимально аккуратно: учитывай видимые ингредиенты, "
                    "размер тарелки, порцию, способ приготовления, масло и соусы. "
                    "Если порция непонятна, сделай реалистичную оценку и поставь confidence low или medium. "
                    "Если на фото не еда и не напиток, верни is_food=false и не выдумывай калории. "
                    "Верни только JSON без markdown."
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            },
        ]
        return await self._estimate(content)

    async def estimate_with_portion(self, previous_description: str, portion: str) -> FoodEstimate:
        content = [
            {
                "type": "text",
                "text": (
                    "Пересчитай калории и БЖУ для еды с учетом уточненной порции. "
                    "Если уточнение не похоже на еду или размер порции, верни is_food=false. "
                    "Верни только JSON без markdown.\n\n"
                    f"Еда: {previous_description}\n"
                    f"Новая порция: {portion}"
                ),
            }
        ]
        return await self._estimate(content)

    async def _estimate(self, user_content: list[dict]) -> FoodEstimate:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты дружелюбный помощник для примерного учета еды. "
                            "Отвечай строго валидным JSON с полями: "
                            "is_food boolean, title string, description string, calories number, protein number, "
                            "fat number, carbs number, confidence low|medium|high, comment string, "
                            "not_food_reason string. "
                            "Не записывай не-еду как еду. Слова без пищевого смысла, предметы, части тела, "
                            "сообщения вроде 'стул', 'привет', 'тест' должны иметь is_food=false. "
                            "Для еды давай реалистичные оценки, не занижай до нуля, если еда распознана. "
                            "Указывай confidence high только когда понятны и состав, и порция."
                        ),
                    },
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
            )
            raw = response.choices[0].message.content or "{}"
            data = json.loads(raw)
            return self._parse_estimate(data)
        except (OpenAIError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.exception("Food recognition failed: %s", exc)
            raise OpenAIRecognitionError from exc

    @staticmethod
    def _parse_estimate(data: dict) -> FoodEstimate:
        is_food = FoodRecognitionClient._parse_bool(data.get("is_food", True))
        if not is_food:
            reason = str(data.get("not_food_reason") or data.get("comment") or "").strip()
            raise NotFoodError(reason)

        confidence = str(data.get("confidence", "medium")).lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"
        return FoodEstimate(
            is_food=True,
            title=str(data["title"]).strip()[:120] or "Еда",
            description=str(data.get("description", "")).strip()[:500],
            calories=max(0, float(data["calories"])),
            protein=max(0, float(data["protein"])),
            fat=max(0, float(data["fat"])),
            carbs=max(0, float(data["carbs"])),
            confidence=confidence,
            comment=str(data.get("comment", "")).strip()[:300],
            not_food_reason="",
        )

    @staticmethod
    def _parse_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "нет", "не еда"}
        return bool(value)
