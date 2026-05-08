from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Boolean, Integer, String, DateTime


class Base(DeclarativeBase):
    pass


class Activists(Base):
    __tablename__ = "activists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_username: Mapped[str] = mapped_column(String)

    fio: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    studak: Mapped[int] = mapped_column(Integer)
    group: Mapped[str] = mapped_column(String)
    phone: Mapped[str] = mapped_column(String) # пусть будет лучше в стрингах храниться
    clothes_size: Mapped[int] = mapped_column(Integer)
    someone_div: Mapped[str] = mapped_column(String)
    birthday: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean)
    ik_div: Mapped[str] = mapped_column(String)


class Mafia(Base):
    __tablename__ = "mafia"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

class Quotes(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)

    tg_id: Mapped[str] = mapped_column(String)
    tg_username: Mapped[str] = mapped_column(String)
    
    text_of_quotes: Mapped[str] = mapped_column()