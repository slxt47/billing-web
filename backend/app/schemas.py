"""Pydantic-Schemas für Request/Response."""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class ItemIn(BaseModel):
    description: str = Field(..., min_length=1, max_length=300)
    quantity: float = Field(1, gt=0)
    unit_price: float = Field(0, ge=0)


class ItemOut(ItemIn):
    id: int
    line_total: float

    class Config:
        from_attributes = True


class InvoiceIn(BaseModel):
    customer_name: str = Field(..., min_length=1, max_length=200)
    customer_address: str = ""
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    tax_rate: float = Field(20, ge=0, le=100)
    notes: str = ""
    skonto_percent: float = Field(0, ge=0, le=100)
    skonto_days: int = Field(0, ge=0)
    discount_percent: float = Field(0, ge=0, le=100)
    small_business: bool = False
    items: list[ItemIn] = Field(..., min_length=1)


class InvoiceOut(BaseModel):
    id: int
    number: str
    customer_name: str
    customer_address: str
    issue_date: date
    due_date: Optional[date]
    tax_rate: float
    notes: str
    status: str
    created_at: datetime
    cancelled_at: Optional[datetime]
    items: list[ItemOut]
    subtotal: float
    discount_percent: float
    discount_amount: float
    net: float
    small_business: bool
    tax_amount: float
    total: float
    paid_amount: float
    remaining: float
    is_overdue: bool
    skonto_percent: float
    skonto_days: int
    skonto_amount: float
    skonto_total: float
    skonto_date: Optional[date]

    class Config:
        from_attributes = True


class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0)


class SettingsIn(BaseModel):
    company_name: str = ""
    address: str = ""
    tax_id: str = ""
    vat_id: str = ""
    iban: str = ""
    bic: str = ""
    email: str = ""
    phone: str = ""


class SettingsOut(SettingsIn):
    has_logo: bool = False

    class Config:
        from_attributes = True


class StatusUpdate(BaseModel):
    status: str


class CustomerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    address: str = ""
    email: str = ""
    payment_term_days: int = Field(14, ge=0)
    skonto_percent: float = Field(0, ge=0, le=100)
    skonto_days: int = Field(0, ge=0)


class CustomerOut(CustomerIn):
    id: int

    class Config:
        from_attributes = True


class ProductIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    unit_price: float = Field(0, ge=0)


class ProductOut(ProductIn):
    id: int

    class Config:
        from_attributes = True


class EmailRequest(BaseModel):
    to: str = Field(..., min_length=3, max_length=200)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=4, max_length=200)
    is_admin: bool = False


class UserOut(BaseModel):
    id: int
    username: str
    is_admin: bool

    class Config:
        from_attributes = True


class PasswordReset(BaseModel):
    password: str = Field(..., min_length=4, max_length=200)
