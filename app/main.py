from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from fastapi.middleware.cors import CORSMiddleware

from .database import items
from .digilocker import router as digilocker_router
from .employees import router as employees_router
from .login import router as login_router
from .orders import router as orders_router
from .plans import router as plans_router
from .products import router as products_router
from .profile import router as profile_router
from .queries import router as queries_router

app = FastAPI(
    title="Junction Backend",
    description="A small CRUD API backed by MongoDB.",
    version="1.0.0",
)

# Allow frontend domains (Netlify, Vercel, local dev)
origins = [    # if you also use backoffice
    
    "http://localhost:4211",  # local dev testing
    "https://junction-frontweb.vercel.app",
    "http://localhost:4200",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # domains allowed to call your backend
    allow_credentials=True,
    allow_methods=["*"],            # GET, POST, PUT, DELETE, etc.
    allow_headers=["*"],            # headers like Authorization, Content-Type
)
app.include_router(login_router)
app.include_router(digilocker_router)
app.include_router(profile_router)
app.include_router(products_router)
app.include_router(employees_router)
app.include_router(orders_router)
app.include_router(queries_router)
app.include_router(plans_router)


class ItemCreate(BaseModel):
    store_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    price: float = Field(ge=0)


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    price: float | None = Field(default=None, ge=0)


class Item(ItemCreate):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    created_at: datetime
    updated_at: datetime


def serialize_item(document: dict) -> Item:
    return Item(
        id=str(document["_id"]),
        store_id=document["store_id"],
        name=document["name"],
        description=document.get("description"),
        price=document["price"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def object_id(item_id: str) -> ObjectId:
    if not ObjectId.is_valid(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    return ObjectId(item_id)


@app.post("/items", response_model=Item, status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate) -> Item:
    now = datetime.now(timezone.utc)
    document = {**payload.model_dump(), "created_at": now, "updated_at": now}
    result = items.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize_item(document)


@app.get("/items/{item_id}", response_model=Item)
def read_item(item_id: str) -> Item:
    document = items.find_one({"_id": object_id(item_id)})
    if document is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return serialize_item(document)


@app.put("/items/{item_id}", response_model=Item)
def update_item(item_id: str, payload: ItemUpdate) -> Item:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")
    changes["updated_at"] = datetime.now(timezone.utc)
    document = items.find_one_and_update(
        {"_id": object_id(item_id)},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return serialize_item(document)


@app.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: str) -> Response:
    result = items.delete_one({"_id": object_id(item_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
