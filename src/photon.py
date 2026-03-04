import requests
import logging


logger = logging.getLogger(__name__)

photonInstances = {
    "trainlog": "https://photon.srv.trainlog.me",
    "chiel": "https://photon.chiel.uk",
    "komoot": "https://photon.komoot.io",
}


def photonRequestSingle(instance, endpoint, params, *, timeout=5):
    _, url = instance
    endpoint = endpoint.lstrip("/")
    resp = requests.get(f"{url}/{endpoint}", params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def photonRequest(endpoint, params, *, timeout=5):
    for instance in photonInstances.items():
        try:
            response_json = photonRequestSingle(
                instance, endpoint, params, timeout=timeout
            )
            return response_json
        except Exception as e:
            logger.debug(f"Photon request failed: {e}")
            continue
    return None
