"""FastAPI application with Playwright lifecycle management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from .api.routes import router
from .core.config import config
from .services.classification import ClassificationSolver
from .services.geetest import GeetestSolver
from .services.hcaptcha import HCaptchaSolver
from .services.recognition import CaptchaRecognizer
from .services.recaptcha_v2 import RecaptchaV2Solver
from .services.recaptcha_v3 import RecaptchaV3Solver
from .services.task_manager import task_manager
from .services.turnstile import TurnstileSolver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_RECAPTCHA_V3_TYPES = [
    "RecaptchaV3TaskProxyless",
    "RecaptchaV3TaskProxylessM1",
    "RecaptchaV3TaskProxylessM1S7",
    "RecaptchaV3TaskProxylessM1S9",
    "RecaptchaV3EnterpriseTask",
    "RecaptchaV3EnterpriseTaskM1",
]

_RECAPTCHA_V2_TYPES = [
    "NoCaptchaTaskProxyless",
    "RecaptchaV2TaskProxyless",
    "RecaptchaV2EnterpriseTaskProxyless",
]

_HCAPTCHA_TYPES = [
    "HCaptchaTaskProxyless",
]

_TURNSTILE_TYPES = [
    "TurnstileTaskProxyless",
    "TurnstileTaskProxylessM1",
]

_GEETEST_TYPES = [
    "GeeTestTaskProxyless",
]

_CLASSIFICATION_TYPES = [
    "HCaptchaClassification",
    "ReCaptchaV2Classification",
    "FunCaptchaClassification",
    "AwsClassification",
]

_IMAGE_TEXT_TYPES = [
    "ImageToTextTask",
    "ImageToTextTaskMuggle",
    "ImageToTextTaskM1",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ── startup ──
    v3_solver = RecaptchaV3Solver(config)
    await v3_solver.start()
    for task_type in _RECAPTCHA_V3_TYPES:
        task_manager.register_solver(task_type, v3_solver)
    log.info("Registered reCAPTCHA v3 solver for types: %s", _RECAPTCHA_V3_TYPES)

    v2_solver = RecaptchaV2Solver(config)
    await v2_solver.start()
    for task_type in _RECAPTCHA_V2_TYPES:
        task_manager.register_solver(task_type, v2_solver)
    log.info("Registered reCAPTCHA v2 solver for types: %s", _RECAPTCHA_V2_TYPES)

    hcaptcha_solver = HCaptchaSolver(config)
    await hcaptcha_solver.start()
    for task_type in _HCAPTCHA_TYPES:
        task_manager.register_solver(task_type, hcaptcha_solver)
    log.info("Registered hCaptcha solver for types: %s", _HCAPTCHA_TYPES)

    turnstile_solver = TurnstileSolver(config)
    await turnstile_solver.start()
    for task_type in _TURNSTILE_TYPES:
        task_manager.register_solver(task_type, turnstile_solver)
    log.info("Registered Turnstile solver for types: %s", _TURNSTILE_TYPES)

    geetest_solver = GeetestSolver(config)
    await geetest_solver.start()
    for task_type in _GEETEST_TYPES:
        task_manager.register_solver(task_type, geetest_solver)
    log.info("Registered GeeTest solver for types: %s", _GEETEST_TYPES)

    recognizer = CaptchaRecognizer(config)
    for task_type in _IMAGE_TEXT_TYPES:
        task_manager.register_solver(task_type, recognizer)
    log.info("Registered image captcha recognizer for types: %s", _IMAGE_TEXT_TYPES)

    classifier = ClassificationSolver(config)
    for task_type in _CLASSIFICATION_TYPES:
        task_manager.register_solver(task_type, classifier)
    log.info("Registered classification solver for types: %s", _CLASSIFICATION_TYPES)

    yield
    # ── shutdown ──
    await v3_solver.stop()
    await v2_solver.stop()
    await hcaptcha_solver.stop()
    await turnstile_solver.stop()
    await geetest_solver.stop()


app = FastAPI(
    title="Captcha Solver Service",
    version="3.0.0",
    description="YesCaptcha-compatible captcha solving service for flow2api.",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/")
async def root() -> dict[str, object]:
    return {
        "service": "captcha-solver",
        "version": "3.0.0",
        "endpoints": {
            "createTask": "/createTask",
            "getTaskResult": "/getTaskResult",
            "getBalance": "/getBalance",
            "health": "/api/v1/health",
        },
        "supported_task_types": task_manager.supported_types(),
    }
