from app import app
from settings import get_api_host, get_api_port


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=get_api_host(), port=get_api_port(), reload=True)
