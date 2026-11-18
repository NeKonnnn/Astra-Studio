import os
import logging
import torch
import yaml
import tempfile
import traceback
from typing import Optional, Dict, Any
from app.core.config import settings

logger = logging.getLogger(__name__)

# Глобальные переменные для хранения моделей диаризации
diarization_pipeline: Optional[Any] = None

print(f"[Handler MODULE] Loaded, PID={os.getpid()}, id(module globals)={id(globals())}", flush=True)


def _load_pipeline_sync():
    """Синхронная загрузка пайплайна (вся тяжёлая работа)"""
    print(f"[Handler] _load_pipeline_sync START, PID={os.getpid()}", flush=True)
    
    try:
        from pyannote.audio import Pipeline
        print("[Handler] pyannote.audio imported OK", flush=True)
    except Exception as e:
        print(f"[Handler] FAILED to import pyannote.audio: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return None
    
    config_path = settings.diarization.config_path
    if not os.path.exists(config_path):
        print(f"[Handler] Config NOT FOUND: {config_path}", flush=True)
        return None
    
    device = settings.diarization.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[Handler] Device: {device}", flush=True)
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f.read())
        print(f"[Handler] Config loaded, keys: {list(config.keys())}", flush=True)
        
        models_dir = settings.diarization.models_dir
        if 'pipeline' in config and 'params' in config['pipeline']:
            params = config['pipeline']['params']
            
            # Исправляем embedding путь
            if 'embedding' in params and isinstance(params['embedding'], str):
                ep = params['embedding']
                if os.path.isabs(ep) or ':' in ep:
                    params['embedding'] = os.path.join(models_dir, "models", os.path.basename(ep))
                elif not os.path.isabs(ep):
                    params['embedding'] = os.path.join(models_dir, ep)
                print(f"[Handler] embedding path: {params['embedding']}", flush=True)
                print(f"[Handler] embedding exists: {os.path.exists(params['embedding'])}", flush=True)
            
            # Исправляем segmentation путь
            if 'segmentation' in params and isinstance(params['segmentation'], str):
                sp = params['segmentation']
                if os.path.isabs(sp) or ':' in sp:
                    params['segmentation'] = os.path.join(models_dir, "models", os.path.basename(sp))
                elif not os.path.isabs(sp):
                    params['segmentation'] = os.path.join(models_dir, sp)
                print(f"[Handler] segmentation path: {params['segmentation']}", flush=True)
                print(f"[Handler] segmentation exists: {os.path.exists(params['segmentation'])}", flush=True)
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as tf:
            yaml.dump(config, tf, default_flow_style=False)
            tmp_path = tf.name
        print(f"[Handler] Temp config: {tmp_path}", flush=True)
        
        print("[Handler] Calling Pipeline.from_pretrained ...", flush=True)
        pipeline = Pipeline.from_pretrained(tmp_path)
        print(f"[Handler] Pipeline loaded: {type(pipeline)}", flush=True)
        
        try:
            os.unlink(tmp_path)
        except:
            pass
        
        if device == "cuda" and torch.cuda.is_available():
            try:
                pipeline = pipeline.to(torch.device("cuda"))
                print("[Handler] Moved to CUDA", flush=True)
            except Exception as cuda_err:
                print(f"[Handler] CUDA failed ({cuda_err}), falling back to CPU", flush=True)
                # Pipeline уже на CPU после from_pretrained, просто продолжаем
        
        print(f"[Handler] SUCCESS, pipeline={pipeline is not None}", flush=True)
        return pipeline
        
    except Exception as e:
        print(f"[Handler] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return None


async def get_diarization_handler() -> Optional[Any]:
    """Получение экземпляра пайплайна диаризации"""
    global diarization_pipeline
    
    print(f"[Handler] get_diarization_handler called, PID={os.getpid()}, pipeline is None: {diarization_pipeline is None}", flush=True)
    
    if not settings.diarization.enabled:
        return None
    
    if diarization_pipeline is not None:
        return diarization_pipeline
    
    # Ленивая загрузка
    diarization_pipeline = _load_pipeline_sync()
    print(f"[Handler] After load: pipeline is None: {diarization_pipeline is None}", flush=True)
    
    return diarization_pipeline


async def cleanup_diarization_handler():
    """Очистка ресурсов пайплайна диаризации"""
    global diarization_pipeline
    
    if diarization_pipeline is not None:
        logger.info("Освобождение ресурсов пайплайна диаризации")
        del diarization_pipeline
        diarization_pipeline = None
        
        # Очищаем кэш CUDA если используется
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


async def reload_diarization_handler():
    """Сброс и повторная загрузка пайплайна диаризации."""
    await cleanup_diarization_handler()
    return await get_diarization_handler()