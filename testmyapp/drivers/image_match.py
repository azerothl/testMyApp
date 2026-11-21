"""Recherche d'un modèle dans une capture de la zone cliente Windows."""

from pathlib import Path


class ImageTemplate:
    def __init__(self, spec: str | dict) -> None:
        import cv2
        import numpy as np

        spec = {"path": spec} if isinstance(spec, str) else spec
        path = Path(spec["path"])
        self.threshold = spec.get("confidence", .93)
        self.region = spec.get("region")
        # imdecode accepte aussi les chemins Windows avec des caractères non ASCII.
        data = path.read_bytes()
        if not data:
            raise ValueError(f"Image vide : {path.name}")
        try:
            self.pixels = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error as exc:
            raise ValueError(f"Image illisible : {path.name}") from exc
        if self.pixels is None or self.pixels.std(axis=(0, 1)).max() < 1:
            raise ValueError(f"Image illisible ou trop uniforme : {path.name}")

    def locate(self, image) -> dict | None:
        import cv2
        import numpy as np

        ox, oy, width, height = self.region or (0, 0, *image.size)
        th, tw = self.pixels.shape[:2]
        if (width < tw or height < th or ox + width > image.width or oy + height > image.height):
            return None
        rgb = image.crop((ox, oy, ox + width, oy + height)).convert("RGB")
        bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
        scores = cv2.matchTemplate(bgr, self.pixels, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, (x, y) = cv2.minMaxLoc(scores)
        if not self.threshold <= confidence <= 1:
            return None

        # Un deuxième objet distinct au-dessus du seuil rend le choix ambigu.
        scores[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1
        if float(scores.max()) >= self.threshold:
            return None
        return {"confidence": round(confidence, 4), "bounds": [ox + x, oy + y, tw, th]}
