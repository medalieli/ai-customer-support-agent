from datetime import datetime

from pydantic import BaseModel, Field


class ContactUpsert(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    lifecycle_stage: str = "lead"
    locale: str | None = None
    company: str | None = None


class Contact(ContactUpsert):
    external_ref: str
    provider_status: str = "active"
    version: str
    created_at: datetime
    updated_at: datetime


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=65536)


class Note(BaseModel):
    external_ref: str
    contact_ref: str
    body: str
    created_at: datetime


class LeadUpsert(BaseModel):
    contact_ref: str = Field(min_length=1, max_length=160)
    interest: str = Field(min_length=2, max_length=200)
    business_need: str = Field(min_length=2, max_length=1000)
    budget_range: str | None = Field(default=None, max_length=80)
    timeline: str | None = Field(default=None, max_length=80)
    preferred_contact_method: str = Field(pattern=r"^(email|phone|video_call)$")


class Lead(LeadUpsert):
    external_ref: str
    status: str = "open"
    version: str
    created_at: datetime
    updated_at: datetime
