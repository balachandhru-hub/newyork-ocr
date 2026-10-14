from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from parent import router as parent_router
import uvicorn
from  s4_data_apis import  start_s4_scheduler, engine as s4_data_apis_engine
from newyork_ocr import start_newyork_sftp_scheduler, start_newyork_email_fetch_scheduler

app = FastAPI()


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    for schema in openapi_schema.get("components", {}).get("schemas", {}).values():
        for prop in schema.get("properties", {}).values():
            items = prop.get("items", {})
            if items.get("contentMediaType"):
                items.pop("contentMediaType")
                items["format"] = "binary"
            if prop.get("contentMediaType"):
                prop.pop("contentMediaType")
                prop["format"] = "binary"
    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = custom_openapi

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://localhost:5000",
        "http://localhost:5000",
        "https://localhost:3000",
        "http://localhost:3000",
        "http://localhost:3001",
        "https://host-ticket-system.chervicaon.com",
        "http://host-ticket-system.chervicaon.com",
        "https://ags-global-support.chervicaon.com",
        "http://localhost:5173",
        "https://localhost:5173","https://sila-fatoora.chervicaon.com",
        "https://sila-fatoora-spain.chervicaon.com",
         "https://spain-sila-fatoora.chervicaon.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(parent_router)


@app.on_event("startup")
def _start_background_schedulers():
    start_newyork_sftp_scheduler()
    start_newyork_email_fetch_scheduler()
    start_s4_scheduler()


@app.on_event("shutdown")
async def _dispose_db_engines():
    await s4_data_apis_engine.dispose()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7778)