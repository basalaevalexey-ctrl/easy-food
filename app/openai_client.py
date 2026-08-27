import base64
import json
import logging

import httpx
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
    def __init__(self, api_key: str, model: str, proxy_url: str = "") -> None:
        timeout = httpx.Timeout(50.0, connect=10.0, read=45.0, write=30.0, pool=5.0)
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        self._http_client: httpx.AsyncClient | None = httpx.AsyncClient(
            proxy=proxy_url or None,
            timeout=timeout,
            limits=limits,
        )
        if proxy_url:
            logger.info("OpenAI proxy is enabled")
        self.client = AsyncOpenAI(
            api_key=api_key or "missing",
            http_client=self._http_client,
            max_retries=2,
        )
        self.model = model

    async def close(self) -> None:
        await self.client.close()

    async def estimate_text(self, text: str) -> FoodEstimate:
        normalized_items = self._format_text_items(text)
        content = [
            {
                "type": "text",
                "text": (
                    "Определи, описывает ли пользователь еду или напиток. "
                    "Если пользователь написал несколько строк, пунктов или блюд через запятую, это один прием пищи: "
                    "обязательно оцени каждую позицию и верни суммарные calories/protein/fat/carbs по всему списку. "
                    "Не останавливайся на первом блюде. "
                    "Если это еда, оцени калории, БЖУ и содержащуюся в еде и напитках воду максимально аккуратно: "
                    "учитывай количество, граммы, штуки, "
                    "тип приготовления, масло, соусы и типичные порции. Если масса не указана, используй обычную "
                    "домашнюю порцию и снизь confidence. "
                    "Если это не еда и не напиток, верни is_food=false и не выдумывай калории. "
                    "В title дай короткое название всего приема пищи, а в description перечисли, что учтено. "
                    "Верни только JSON без markdown.\n\n"
                    f"Сообщение пользователя:\n{text}\n\n"
                    f"{normalized_items}"
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
                    "Если это еда, оцени калории, БЖУ и содержащуюся в блюде воду максимально аккуратно: "
                    "учитывай видимые ингредиенты, "
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
                    "Пересчитай калории, БЖУ и содержащуюся воду для еды с учетом уточненной порции. "
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
                            "fat number, carbs number, water_ml number, confidence low|medium|high, comment string, "
                            "not_food_reason string. "
                            "Не записывай не-еду как еду. Слова без пищевого смысла, предметы, части тела, "
                            "сообщения вроде 'стул', 'привет', 'тест' должны иметь is_food=false. "
                            "Для еды давай реалистичные оценки, не занижай до нуля, если еда распознана. "
                            "Если во входе несколько блюд, строк или продуктов, считай их все как один прием пищи "
                            "и возвращай сумму по всем позициям. Не игнорируй кофе, напитки, сахар, сыр, соусы, хлеб "
                            "и маленькие добавки. В description напиши краткую разбивку вида: "
                            "'Учтено: скрембл из 2 яиц; кофе с молоком и 2 ложками сахара; 2 бутерброда с сыром'. "
                            "water_ml — примерный объем воды в самом блюде и напитках, а не совет, сколько допить. "
                            "Учитывай воду в супах, напитках, молочных продуктах, овощах и фруктах. "
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
            water_ml=min(3000, max(0, float(data.get("water_ml") or 0))),
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

    @staticmethod
    def _format_text_items(text: str) -> str:
        lines = [line.strip(" -•\t") for line in text.splitlines() if line.strip(" -•\t")]
        if len(lines) <= 1:
            return ""
        items = "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))
        return f"Позиции для обязательного учета:\n{items}"
