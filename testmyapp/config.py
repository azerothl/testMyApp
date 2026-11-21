"""Chargement et validation des fichiers TOML (spec §13).

Formats : personas/*.toml, targets/*.toml, scenarios/*.toml,
campaigns/*.toml. Toute erreur de structure lève :class:`ConfigError` avec
un message exploitable. Les valeurs manquantes prennent des défauts
cohérents avec la spec (§4.1 pour les personas).
"""
from __future__ import annotations

import tomllib
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


def load_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Fichier introuvable : {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML invalide dans {path} : {exc}") from exc


# ---------------------------------------------------------------- personas
VISION_VALUES = {"normal", "low", "blind", "deuteranopia", "protanopia", "tritanopia"}
MOTOR_VALUES = {"normal", "tremor", "keyboard_only"}
NETWORK_PROFILES = {"fiber", "dsl", "4g", "3g", "slow-3g", "offline-flaky"}


@dataclass
class Persona:
    id: str
    name: str
    pronouns: str = "elle/il"
    age: int = 35
    occupation: str = ""
    bio: str = ""
    locale: str = "fr-FR"
    timezone: str = ""
    # sprite
    sprite_base: str = "body_a"
    palette: dict[str, str] = field(default_factory=dict)
    accessories: list[str] = field(default_factory=list)
    # a11y
    vision: str = "normal"
    motor: str = "normal"
    reading_wpm: int = 220
    screen_reader: bool = False
    # device
    device_type: str = "desktop"
    screen_w: int = 1440
    screen_h: int = 900
    dpr: float = 1.0
    input_device: str = "mouse"
    network_profile: str = "fiber"
    cpu_throttle: float = 1.0
    browser: str = "chromium"
    # comportement (échelles 0..1 sauf speed.factor)
    patience: float = 0.5
    curiosity: float = 0.5
    tech_savviness: float = 0.5
    error_proneness: float = 0.1
    persistence: float = 0.5
    help_seeking: float = 0.5
    speed_factor: float = 1.0
    on_error: dict[str, float] = field(
        default_factory=lambda: {"retry": 0.5, "explore": 0.2, "ask_help": 0.2, "abandon": 0.1}
    )
    # émotions
    emotion_bias: float = 0.0
    emotion_volatility: float = 1.0
    # objectifs
    goals_primary: list[str] = field(default_factory=list)
    goals_secondary: list[str] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)

    @property
    def interaction_input(self) -> str:
        return "keyboard" if self.motor == "keyboard_only" else self.input_device

    @property
    def retries(self) -> int:
        """Nombre de réessais avant abandon (spec §4.1)."""
        return 1 + round(self.persistence * 4)

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if self.vision not in VISION_VALUES:
            raise ConfigError(f"persona {self.id}: vision invalide '{self.vision}'")
        if self.motor not in MOTOR_VALUES:
            raise ConfigError(f"persona {self.id}: motor invalide '{self.motor}'")
        if self.network_profile not in NETWORK_PROFILES:
            raise ConfigError(f"persona {self.id}: network.profile invalide '{self.network_profile}'")
        if self.vision == "blind" and self.screen_reader is False:
            warnings.append(f"persona {self.id}: vision=blind sans screen_reader — incohérent")
        if self.motor == "keyboard_only" and self.input_device == "touch":
            warnings.append(f"persona {self.id}: keyboard_only + input touch — incohérent")
        return warnings


def persona_from_toml(data: dict[str, Any], source: str = "<dict>") -> Persona:
    if "id" not in data:
        raise ConfigError(f"{source}: champ 'id' manquant")

    def grab(key: str, default: Any) -> Any:
        return data.get(key, default)

    device = data.get("device", {})
    a11y = data.get("a11y", {})
    sprite = data.get("sprite", {})
    behavior = data.get("behavior", {})
    emotion = data.get("emotion", {})
    goals = data.get("goals", {})
    return Persona(
        id=data["id"],
        name=str(grab("name", data["id"])),
        pronouns=str(grab("pronouns", "elle/il")),
        age=int(grab("age", 35)),
        occupation=str(grab("occupation", "")),
        bio=str(grab("bio", "")),
        locale=str(grab("locale", "fr-FR")),
        timezone=str(grab("timezone", "")),
        sprite_base=str(sprite.get("base", "body_a")),
        palette=dict(sprite.get("palette", {})),
        accessories=list(sprite.get("accessories", [])),
        vision=str(a11y.get("vision", "normal")),
        motor=str(a11y.get("motor", "normal")),
        reading_wpm=int(a11y.get("reading_wpm", 220)),
        screen_reader=bool(a11y.get("screen_reader", False)),
        device_type=str(device.get("type", "desktop")),
        screen_w=int(device.get("screen", {}).get("w", 1440)),
        screen_h=int(device.get("screen", {}).get("h", 900)),
        dpr=float(device.get("screen", {}).get("dpr", 1.0)),
        input_device=str(device.get("input", "mouse")),
        network_profile=str(device.get("network", {}).get("profile", "fiber"))
        or str(device.get("network_profile", "fiber")),
        cpu_throttle=float(device.get("cpu", {}).get("throttle", 1.0)),
        browser=str(device.get("browser", "chromium")),
        patience=float(behavior.get("patience", 0.5)),
        curiosity=float(behavior.get("curiosity", 0.5)),
        tech_savviness=float(behavior.get("tech_savviness", 0.5)),
        error_proneness=float(behavior.get("error_proneness", 0.1)),
        persistence=float(behavior.get("persistence", 0.5)),
        help_seeking=float(behavior.get("help_seeking", 0.5)),
        speed_factor=float(behavior.get("speed", {}).get("factor", 1.0)),
        on_error=dict(behavior.get("on_error", {})),
        emotion_bias=float(emotion.get("bias", 0.0)),
        emotion_volatility=float(emotion.get("volatility", 1.0)),
        goals_primary=[g["text"] if isinstance(g, dict) else str(g) for g in goals.get("primary", [])],
        goals_secondary=[g["text"] if isinstance(g, dict) else str(g) for g in goals.get("secondary", [])],
        knowledge=[str(k) for k in data.get("knowledge", [])],
        misconceptions=[str(m) for m in data.get("misconceptions", [])],
    )


def persona_to_toml_dict(p: Persona) -> dict[str, Any]:
    """Inverse de persona_from_toml (pour export/génération)."""
    return {
        "schema_version": 1,
        "id": p.id,
        "name": p.name,
        "pronouns": p.pronouns,
        "age": p.age,
        "occupation": p.occupation,
        "bio": p.bio,
        "locale": p.locale,
        "timezone": p.timezone,
        "sprite": {"base": p.sprite_base, "palette": p.palette, "accessories": p.accessories},
        "a11y": {"vision": p.vision, "motor": p.motor, "reading_wpm": p.reading_wpm, "screen_reader": p.screen_reader},
        "device": {
            "type": p.device_type,
            "screen": {"w": p.screen_w, "h": p.screen_h, "dpr": p.dpr},
            "input": p.input_device,
            "network": {"profile": p.network_profile},
            "cpu": {"throttle": p.cpu_throttle},
            "browser": p.browser,
        },
        "behavior": {
            "patience": p.patience,
            "curiosity": p.curiosity,
            "tech_savviness": p.tech_savviness,
            "error_proneness": p.error_proneness,
            "persistence": p.persistence,
            "help_seeking": p.help_seeking,
            "speed": {"factor": p.speed_factor},
            "on_error": p.on_error,
        },
        "emotion": {"bias": p.emotion_bias, "volatility": p.emotion_volatility},
        "goals": {"primary": [{"text": g} for g in p.goals_primary], "secondary": [{"text": g} for g in p.goals_secondary]},
        "knowledge": p.knowledge,
        "misconceptions": p.misconceptions,
    }


# ----------------------------------------------------------------- targets
@dataclass
class Target:
    id: str
    type: str = "web"  # web | desktop | sim
    base_url: str = ""
    executable: str = ""
    browser: str = "chromium"
    args: list[str] = field(default_factory=list)
    window_title: str = ""
    templates: dict[str, str | dict] = field(default_factory=dict)
    env_credentials: dict[str, str] = field(default_factory=dict)  # nom -> nom de var d'env
    notes: str = ""
    discovery_bridge: bool = False
    storage_state: str = ""

    @property
    def supports_free_tasks(self) -> bool:
        return self.type == "web" or self.discovery_bridge

    @classmethod
    def from_toml(cls, data: dict[str, Any], source: str = "<dict>") -> "Target":
        if "id" not in data:
            raise ConfigError(f"{source}: champ 'id' manquant")
        ttype = data.get("type", "web")
        if ttype not in {"web", "desktop", "sim"}:
            raise ConfigError(f"{source}: type de cible inconnu '{ttype}'")
        if ttype == "web" and not data.get("base_url"):
            raise ConfigError(f"{source}: cible web sans base_url")
        discovery_bridge = data.get("discovery_bridge", False)
        if type(discovery_bridge) is not bool or (discovery_bridge and ttype != "desktop"):
            raise ConfigError(f"{source}: discovery_bridge est un booléen réservé aux cibles Windows")
        storage_state = data.get("storage_state", "")
        if not isinstance(storage_state, str) or (storage_state and ttype != "web"):
            raise ConfigError(f"{source}: storage_state est un chemin réservé aux cibles Web")
        if storage_state:
            storage_state = str((Path(source).parent / storage_state).resolve())
        templates = {}
        configured_templates = data.get("templates", {})
        if not isinstance(configured_templates, dict):
            raise ConfigError(f"{source}: section [templates] attendue")
        for name, entry in configured_templates.items():
            spec = {"path": entry} if isinstance(entry, str) else entry
            if not isinstance(spec, dict) or not isinstance(spec.get("path"), str) or not spec["path"].strip():
                raise ConfigError(f"{source}: chemin d’image manquant pour {name!r}")
            confidence = spec.get("confidence", .93)
            region = spec.get("region")
            if type(confidence) not in (int, float) or not 0 < confidence <= 1:
                raise ConfigError(f"{source}: confiance invalide pour {name!r}")
            if region is not None and (not isinstance(region, list) or len(region) != 4
                    or any(type(n) is not int for n in region) or min(region[:2]) < 0 or min(region[2:]) <= 0):
                raise ConfigError(f"{source}: région attendue [x, y, largeur, hauteur] pour {name!r}")
            path = Path(spec["path"])
            if not path.is_absolute() and source != "<dict>":
                path = Path(source).parent / path
            templates[name] = {"path": str(path.resolve()), "confidence": confidence, "region": region}
        return cls(
            id=data["id"],
            type=ttype,
            base_url=str(data.get("base_url", "")),
            executable=str(data.get("executable", "")),
            browser=str(data.get("browser", "chromium")),
            args=list(data.get("args", [])),
            window_title=str(data.get("window", {}).get("title", "")),
            templates=templates,
            discovery_bridge=discovery_bridge,
            storage_state=storage_state,
            env_credentials=dict(data.get("credentials", {})),
            notes=str(data.get("notes", "")),
        )


# --------------------------------------------------------------- scenarios
@dataclass
class Expectation:
    kind: str  # visible | url_matches | metric_below | element_count
    selector: str = ""
    pattern: str = ""
    metric: str = ""
    max: float = 0.0
    min_count: int = 0
    timeout_ms: int = 8000
    api: dict = field(default_factory=dict)
    failure_text: str = ""

    def __post_init__(self) -> None:
        required = {"visible": "selector", "text_contains": "selector", "url_matches": "pattern",
                    "metric_below": "metric", "element_count": None, "api_record": None}
        if self.kind not in required:
            raise ConfigError(f"type d'attente inconnu : {self.kind!r}")
        field_name = required[self.kind]
        if field_name and (not isinstance(getattr(self, field_name), str)
                           or not getattr(self, field_name).strip()):
            raise ConfigError(f"attente {self.kind} : {field_name} est obligatoire")
        if type(self.timeout_ms) is not int or self.timeout_ms <= 0:
            raise ConfigError("timeout_ms doit être un entier positif")
        if type(self.min_count) is not int or self.min_count < 0:
            raise ConfigError("min_count doit être un entier positif ou nul")
        if type(self.max) not in (int, float) or not 0 <= self.max < float("inf"):
            raise ConfigError("max doit être un nombre fini positif ou nul")
        if self.kind == "api_record":
            rule = self.api
            if (not isinstance(rule, dict) or not isinstance(rule.get("endpoint"), str)
                    or not rule["endpoint"].startswith("/") or rule["endpoint"].startswith("//")
                    or "\\" in rule["endpoint"] or not isinstance(rule.get("collection"), str)
                    or not isinstance(rule.get("match"), dict) or not rule["match"]
                    or type(rule.get("count", 1)) is not int or rule.get("count", 1) < 0
                    or ("save_as" in rule and (not isinstance(rule["save_as"], str) or rule.get("count", 1) != 1))):
                raise ConfigError("api_record : endpoint local, collection et critères de correspondance attendus")


@dataclass
class Step:
    intent: str
    mode: str = "scripted"  # scripted | explore | agent
    actions: list[dict[str, Any]] = field(default_factory=list)
    expects: list[Expectation] = field(default_factory=list)
    baseline_seconds: float = 10.0
    screenshot: str = "after"  # none | before | after | both
    critical: bool = True
    explore_budget: int = 8
    action_paths: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    stop_on_failure: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"scripted", "explore", "agent", "discovery"}:
            raise ConfigError(f"mode d'étape inconnu : {self.mode!r}")
        if self.screenshot not in {"none", "before", "after", "both"}:
            raise ConfigError(f"option de capture inconnue : {self.screenshot!r}")
        if type(self.critical) is not bool:
            raise ConfigError("critical doit être un booléen")
        if type(self.stop_on_failure) is not bool:
            raise ConfigError("stop_on_failure doit être un booléen")
        if type(self.explore_budget) is not int or not 1 <= self.explore_budget <= 50:
            raise ConfigError("explore_budget doit être un entier entre 1 et 50")
        if not isinstance(self.action_paths, dict) or (self.action_paths and self.mode != "scripted"):
            raise ConfigError("action_paths est réservé aux étapes scriptées")
        for key, actions in self.action_paths.items():
            kind, _, value = key.partition(":") if isinstance(key, str) else ("", "", "")
            if not ((kind == "persona" and value) or (kind == "input" and value in {"keyboard", "mouse", "touch"})):
                raise ConfigError(f"Variante inconnue : {key} (utilisez persona:<id> ou input:<mode>)")
            if not isinstance(actions, list) or not actions or any(
                    not isinstance(action, dict) or not isinstance(action.get("type"), str) or not action["type"]
                    for action in actions):
                raise ConfigError(f"La variante {key} doit contenir au moins une action avec un type")


@dataclass
class Scenario:
    id: str
    name: str = ""
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    task_text: str | None = None

    @classmethod
    def from_toml(cls, data: dict[str, Any], source: str = "<dict>") -> "Scenario":
        if "id" not in data:
            raise ConfigError(f"{source}: champ 'id' manquant")
        steps: list[Step] = []
        for i, raw in enumerate(data.get("steps", [])):
            try:
                expects = [Expectation(**e) for e in raw.get("expects", [])]
                steps.append(Step(
                    intent=str(raw.get("intent", f"étape {i + 1}")),
                    mode=str(raw.get("mode", "scripted")),
                    actions=list(raw.get("actions", [])),
                    expects=expects,
                    baseline_seconds=float(raw.get("baseline_seconds", 10.0)),
                    screenshot=str(raw.get("screenshot", "after")),
                    critical=raw.get("critical", True),
                    stop_on_failure=raw.get("stop_on_failure", False),
                    explore_budget=raw.get("explore_budget", 8),
                    action_paths=raw.get("action_paths", {}),
                ))
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{source}, étape {i + 1} : {exc}") from exc
        if not steps:
            raise ConfigError(f"{source}: scénario sans étape")
        task_text = data.get("task_text")
        if task_text is not None and (not isinstance(task_text, str) or not task_text.strip()):
            raise ConfigError(f"{source}: le message modifiable doit être un texte non vide")
        return cls(
            id=data["id"],
            name=str(data.get("name", data["id"])),
            description=str(data.get("description", "")),
            steps=steps,
            task_text=task_text,
        )


# ---------------------------------------------------------------- campaign
DEFAULT_THRESHOLDS = {
    "lcp_ms": 2500.0,
    "cls": 0.1,
    "inp_ms": 200.0,
    "fcp_ms": 1800.0,
    "console_errors_max": 0,
    "desktop_startup_ms": 3000.0,
    "action_p95_ms": 400.0,
    "contrast_min": 4.5,
    "touch_target_px": 44.0,
}
DEFAULT_WEIGHTS = {"fonctionnel": 0.30, "perf": 0.20, "a11y": 0.15, "ux": 0.20, "stabilite": 0.15}


@dataclass
class AgentSettings:
    endpoint: str = ""
    model: str = ""
    api_key_env: str = ""
    max_requests: int = 4
    timeout_seconds: float = 3.0
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0

    @classmethod
    def from_toml(cls, options: dict, source: str) -> "AgentSettings":
        settings = cls(**{key: value for key, value in options.items() if key != "enabled"})
        endpoint = urlsplit(settings.endpoint)
        if (not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                or (endpoint.scheme != "https" and
                    (endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}))):
            raise ConfigError(f"{source}: fournisseur IA attendu sur HTTPS ou HTTP local")
        if not settings.model or not isinstance(settings.model, str):
            raise ConfigError(f"{source}: ai.model est requis")
        if type(settings.max_requests) is not int or not 1 <= settings.max_requests <= 20:
            raise ConfigError(f"{source}: ai.max_requests doit être compris entre 1 et 20")
        if type(settings.timeout_seconds) not in (int, float) or not .2 <= settings.timeout_seconds <= 30:
            raise ConfigError(f"{source}: ai.timeout_seconds doit être compris entre 0,2 et 30")
        for price in (settings.input_price_per_million, settings.output_price_per_million):
            if type(price) not in (int, float) or not 0 <= price < float("inf"):
                raise ConfigError(f"{source}: tarif de fournisseur IA invalide")
        if settings.api_key_env and (not settings.api_key_env.isidentifier() or not settings.api_key_env.isascii()):
            raise ConfigError(f"{source}: ai.api_key_env doit être un nom de variable d’environnement")
        return settings


@dataclass
class Campaign:
    id: str
    target_id: str
    personas: list[str]
    scenarios: list[str]
    max_workers: int = 3
    speed: str = "realtime"  # realtime | fast | turbo
    seed: int = 42
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    trace: bool = False
    video: bool = False
    ai_mode: bool = False  # M3, off par défaut (spec §16)
    agent: AgentSettings | None = None
    score_pass: float = 70.0  # code retour headless si score < (spec §14)
    discovery: dict[str, str] = field(default_factory=dict)
    depends_on: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_toml(cls, data: dict[str, Any], source: str = "<dict>") -> "Campaign":
        if "id" not in data:
            raise ConfigError(f"{source}: champ 'id' manquant")
        matrix = data.get("matrix", {})
        thresholds = dict(DEFAULT_THRESHOLDS)
        thresholds.update({k: float(v) for k, v in data.get("thresholds", {}).items()})
        weights = dict(DEFAULT_WEIGHTS)
        weights.update({k: float(v) for k, v in data.get("weights", {}).items()})
        total = sum(weights.values())
        if any(not 0 <= value <= 1 for value in weights.values()) or abs(total - 1.0) > 0.01:
            raise ConfigError(f"{source}: la somme des pondérations vaut {total}, attendu ~1.0")
        for axis in ("personas", "scenarios"):
            ids = matrix.get(axis, [])
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids):
                raise ConfigError(f"{source}: matrix.{axis} doit contenir au moins un identifiant")
            if len(ids) != len(set(ids)):
                raise ConfigError(f"{source}: matrix.{axis} contient des doublons")
        ai_cfg = data.get("ai", {})
        discovery = data.get("discovery", {})
        if not isinstance(discovery, dict) or any(not isinstance(sid, str) or not sid for sid in discovery.values()):
            raise ConfigError(f"{source}: discovery doit associer chaque persona à un identifiant de scénario")
        depends_on = data.get("depends_on", {})
        if not isinstance(depends_on, dict) or any(not isinstance(pid, str) or not pid for pid in depends_on.values()):
            raise ConfigError(f"{source}: depends_on doit désigner un persona préalable")
        ai_mode = ai_cfg.get("enabled", False)
        if type(ai_mode) is not bool:
            raise ConfigError(f"{source}: ai.enabled doit être un booléen")
        try:
            agent = AgentSettings.from_toml(ai_cfg, source) if ai_mode else None
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{source}: configuration IA invalide : {exc}") from exc
        return cls(
            id=data["id"],
            target_id=str(data.get("target", "")),
            personas=[str(p) for p in matrix.get("personas", [])],
            scenarios=[str(s) for s in matrix.get("scenarios", [])],
            max_workers=int(data.get("max_workers", 3)),
            speed=str(data.get("speed", "realtime")),
            seed=int(data.get("seed", 42)),
            thresholds=thresholds,
            weights=weights,
            trace=bool(data.get("trace", False)),
            video=bool(data.get("video", False)),
            ai_mode=ai_mode,
            agent=agent,
            score_pass=float(data.get("score_pass", 70.0)),
            discovery=discovery,
            depends_on=depends_on,
        )
