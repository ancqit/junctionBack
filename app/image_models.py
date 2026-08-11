from pydantic import BaseModel, Field, HttpUrl


class ImageResult(BaseModel):
    id: str
    cdn_url: HttpUrl
    thumbnail_url: HttpUrl
    alt: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    source: str = "pexels"
    photographer: str | None = None
    photographer_url: HttpUrl | None = None
