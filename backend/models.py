"""Pydantic request models. All money values are integer cents."""

from pydantic import BaseModel, Field

SKU_MAX = 64
NAME_MAX = 200
SOURCE_MAX = 32
PAYMENT_METHOD_MAX = 32
PIN_MIN = 4
PIN_MAX = 8
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
RATE_BPS_MAX = 10000  # basis points; 10000 bps == 100%


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=NAME_MAX)
    password: str = Field(min_length=1, max_length=NAME_MAX)


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=SKU_MAX)
    name: str = Field(min_length=1, max_length=NAME_MAX)
    qty: int = Field(ge=0, le=999999)
    price_cents: int = Field(ge=0, le=9999999)
    tax_category_id: int | None = Field(default=None, gt=0)


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=SKU_MAX)
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    qty: int | None = Field(default=None, ge=0, le=999999)
    price_cents: int | None = Field(default=None, ge=0, le=9999999)
    tax_category_id: int | None = Field(default=None, gt=0)


class TaxAccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    jurisdiction: str | None = Field(default=None, max_length=NAME_MAX)
    rate_bps: int = Field(ge=0, le=RATE_BPS_MAX)
    effective_from: str = Field(pattern=DATE_PATTERN)
    effective_to: str | None = Field(default=None, pattern=DATE_PATTERN)


class TaxAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    jurisdiction: str | None = Field(default=None, max_length=NAME_MAX)
    rate_bps: int | None = Field(default=None, ge=0, le=RATE_BPS_MAX)
    effective_from: str | None = Field(default=None, pattern=DATE_PATTERN)
    effective_to: str | None = Field(default=None, pattern=DATE_PATTERN)


class TaxCategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)


class TaxCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)


class TaxCategoryAccountsIn(BaseModel):
    tax_account_ids: list[int] = Field(default_factory=list)


class SaleIn(BaseModel):
    product_id: int = Field(gt=0)
    qty: int = Field(gt=0, le=999999)
    unit_price_cents: int | None = Field(default=None, ge=0, le=9999999)


class PosSaleIn(BaseModel):
    """Sale pushed from the external POS terminal, keyed by SKU."""

    sku: str = Field(min_length=1, max_length=SKU_MAX)
    qty: int = Field(gt=0, le=999999)
    unit_price_cents: int | None = Field(default=None, ge=0, le=9999999)


class CheckoutItemIn(BaseModel):
    product_id: int = Field(gt=0)
    qty: int = Field(gt=0, le=999999)


class PosCheckoutIn(BaseModel):
    items: list[CheckoutItemIn] = Field(min_length=1)
    payment_method: str = Field(min_length=1, max_length=PAYMENT_METHOD_MAX)


class EmployeeIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    pin: str = Field(min_length=PIN_MIN, max_length=PIN_MAX, pattern=r"^\d+$")


class ClockInIn(BaseModel):
    employee_id: int = Field(gt=0)
    pin: str = Field(min_length=1, max_length=PIN_MAX)


class ClockOutIn(BaseModel):
    employee_id: int = Field(gt=0)
    pin: str = Field(min_length=1, max_length=PIN_MAX)
