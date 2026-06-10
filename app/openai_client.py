import base64
import json
import logging

from openai import AsyncOpenAI, OpenAIError

from app.models import FoodEstimate

logger = logging.getLogger(__name__)


class OpenAIRecognitionError(Exception):
    pass


class FoodRecognitionClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key or "missing")
        self.model = model

    async def estimate_text(self, text: str) -> FoodEstimate:
        content = [
            {
                "type": "text",
                "text": (
                    "Оцени калории и БЖУ по описанию еды. "
                    "Верни только JSON без markdown.\n\n"
                    f"Еда: {text}"
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
                    "Оцени еду на фото: примерные калории и БЖУ. "
                    "Если порция непонятна, сделай реалистичную оценку и поставь confidence low или medium. "
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
                            "title string, description string, calories number, protein number, "
                            "fat number, carbs number, confidence low|medium|high, comment string. "
                            "Оценки должны быть реалистичными, но не медицинскими рекомендациями."
                        ),
                    },
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
            )
            raw = response.choices[0].message.content or "{}"
            data = json.loads(raw)
            return self._parse_estimate(data)
        except (OpenAIError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.exception("Food recognition failed: %s", exc)
            raise OpenAIRecognitionError from exc

    @staticmethod
    def _parse_estimate(data: dict) -> FoodEstimate:
        confidence = str(data.get("confidence", "medium")).lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"
        return FoodEstimate(
            title=str(data["title"]).strip()[:120] or "Еда",
            description=str(data.get("description", "")).strip()[:500],
            calories=max(0, float(data["calories"])),
            protein=max(0, float(data["protein"])),
            fat=max(0, float(data["fat"])),
            carbs=max(0, float(data["carbs"])),
            confidence=confidence,
            comment=str(data.get("comment", "")).strip()[:300],
        )
