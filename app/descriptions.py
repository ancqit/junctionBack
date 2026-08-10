from fastapi import APIRouter, status
from pydantic import BaseModel, Field, field_validator

from .gemini import generate_text

router = APIRouter(prefix="/descriptions", tags=["descriptions"])

DESCRIPTION_PROMPT = (
    "You are writing product copy for an online shop catalog. "
    "Turn the following short product summary into a clear, detailed product description. "
    "Use complete sentences, highlight key features and benefits, and keep a professional tone. "
    "Do not invent specifications that are not implied by the summary. "
    "Return only the description text with no title, labels, or markdown.\n\n"
    "Summary:\n{text}"
)


class DescriptionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class DescriptionResponse(BaseModel):
    description: str


def generate_description(text: str) -> str:
    return generate_text(DESCRIPTION_PROMPT.format(text=text))


@router.post("/generate", response_model=DescriptionResponse, status_code=status.HTTP_200_OK)
def generate_product_description(payload: DescriptionRequest) -> DescriptionResponse:
    return DescriptionResponse(description=generate_description(payload.text))
