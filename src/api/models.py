"""Shared API response models."""

from typing import Any, Optional

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    success: bool = False
    message: str
    code: Optional[str] = None
    details: Optional[Any] = None


class SuccessResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None
    data: Optional[Any] = None
