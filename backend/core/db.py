from sqlmodel import SQLModel, Session, create_engine

# DATABASE_URL = "sqlite:///G://Share//PANIAGUA DANIEL//OPTICAL//optical_inspector.db"

DATABASE_URL = "sqlite:///optical_inspector.db"
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={
        "check_same_thread": False
    }
)


def get_session():
    with Session(engine) as session:
        yield session


def create_database():
    SQLModel.metadata.create_all(engine)