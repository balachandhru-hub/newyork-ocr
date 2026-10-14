from fastapi import APIRouter
from auth import router as auth_router
from newyork_ocr import router as newyork_ocr_router
from s4_data_apis import router as s4_data_apis_router
from uae_einvoicing.router import router as uae_einvoicing_router
router = APIRouter(prefix="/api/v1")

router.include_router(auth_router)
router.include_router(newyork_ocr_router)
router.include_router(s4_data_apis_router)
router.include_router(uae_einvoicing_router)