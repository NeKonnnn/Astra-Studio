import requests
import os
import argparse
from tqdm import tqdm
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
def download_model(model_url: str, output_path: str):
    """Загрузка модели с прогресс-баром."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    # Проверяем, существует ли уже файл
    if os.path.exists(output_path):
        logger.info(f"Model already exists at {output_path}")
        return
    logger.info(f"Downloading model from {model_url} to {output_path}")
    # Загрузка с прогресс-баром
    response = requests.get(model_url, stream=True)
    total_size = int(response.headers.get('content-length', 0))
    with open(output_path, 'wb') as file, tqdm(
            desc=os.path.basename(output_path),
            total=total_size,
            unit='iB',
            unit_scale=True,
            unit_divisor=1024,
    ) as bar:
        for data in response.iter_content(chunk_size=1024):
            size = file.write(data)
            bar.update(size)
    logger.info("Download completed successfully")
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download a GGUF model file")
    parser.add_argument("--model-url", type=str, required=True, help="URL of the model to download")
    parser.add_argument("--output-path", type=str, required=True, help="Path to save the model")
    args = parser.parse_args()
    download_model(args.model_url, args.output_path)
