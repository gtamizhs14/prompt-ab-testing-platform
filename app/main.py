from fastapi import FastAPI
from app.db.database import Base, engine
from app.api import prompt_routes
from app.api import experiment_routes



app = FastAPI()

Base.metadata.create_all(bind=engine)

app.include_router(prompt_routes.router)
app.include_router(experiment_routes.router)