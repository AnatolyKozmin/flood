"""Флаги функций: что выключено хозяином. Отдельный модуль."""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
