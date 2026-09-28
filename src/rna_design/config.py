from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import DesignError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BackboneConfig(StrictModel):
    path: Path
    chain: str = Field(min_length=1)
    mapping: Literal["exact_sequence"]
    missing_atoms: Literal["error", "mask_phosphate"] = "error"


class ReconstructionConfig(StrictModel):
    design_sequence: Path
    mapping: Path


class InputConfig(StrictModel):
    sequence: Path
    target: Path | None = None
    target_source: Literal["reference_mfe", "provided"] | None = None
    backbone: BackboneConfig | None = None
    reconstruction: ReconstructionConfig | None = None

    @model_validator(mode="after")
    def check_source(self):
        expected = "provided" if self.target else "reference_mfe"
        if self.target_source and self.target_source != expected:
            raise ValueError("target_source contradicts the supplied target file")
        self.target_source = expected
        return self


class ModelConfig(StrictModel):
    name: Literal["grnade"] = "grnade"
    mode: Literal["2d"] = "2d"
    policy: Literal["full_redesign"] = "full_redesign"
    sampling_temperature: float = Field(default=0.8, gt=0, le=10)


class Struct2SeqConfig(StrictModel):
    name: Literal["struct2seq"] = "struct2seq"
    mode: Literal["2d"] = "2d"
    policy: Literal["full_redesign"] = "full_redesign"
    checkpoint_variant: Literal["SHAPE"] = "SHAPE"
    sampler: Literal["native_stochastic"] = "native_stochastic"
    # This is the upstream probability of sampling vs taking argmax, not temperature.
    sampling_probability: float = Field(default=1.0, ge=0, le=1)


class BothModelsConfig(StrictModel):
    name: Literal["both"] = "both"
    grnade: ModelConfig = Field(default_factory=ModelConfig)
    struct2seq: Struct2SeqConfig = Field(default_factory=Struct2SeqConfig)
    failure_policy: Literal["continue", "stop"] = "continue"


class NaMPNNConfig(StrictModel):
    name: Literal["na_mpnn"] = "na_mpnn"
    mode: Literal["fixed_backbone"] = "fixed_backbone"
    policy: Literal["full_redesign"] = "full_redesign"
    sampling_temperature: float = Field(default=0.1, gt=0, le=10)


class AllModelsConfig(BothModelsConfig):
    name: Literal["all"] = "all"
    na_mpnn: NaMPNNConfig = Field(default_factory=NaMPNNConfig)


def generator_names(model):
    name = model["name"] if isinstance(model, dict) else model.name
    if name == "all":
        return ["grnade", "struct2seq", "na_mpnn"]
    return ["grnade", "struct2seq"] if name == "both" else [name]


def generators(model):
    """Stable execution order, with model-specific controls and equal per-model budgets."""
    names = generator_names(model)
    return [getattr(model, name) for name in names] if len(names) > 1 else [model]


class BudgetConfig(StrictModel):
    proposals: int = Field(default=1000, gt=0, strict=True)
    attempt_cap: int = Field(default=2000, gt=0, strict=True)
    wall_time_seconds: float = Field(default=86400, gt=0)
    batch_size: int = Field(default=10, gt=0, le=128, strict=True)
    seed: int = Field(default=42, ge=0, lt=2**32, strict=True)


class RuntimeConfig(StrictModel):
    root: Path = Path(".runtime")
    device: Literal["cpu", "cuda"] = "cpu"
    threads: int = Field(default=2, gt=0, le=64, strict=True)
    request_timeout_seconds: float = Field(default=1800, gt=0)


class EvaluationConfig(StrictModel):
    physical_temperature_c: float = Field(default=37, ge=0, le=100)
    score_definition: Literal["oksp-target-ecs-cpq-v1"] = "oksp-target-ecs-cpq-v1"


class SelectionConfig(StrictModel):
    top_k: int = Field(default=100, ge=0, strict=True)
    max_c_percent: float | None = Field(default=None, ge=0, le=100)
    min_openknot_score: float | None = Field(default=None, ge=0, le=100)
    min_pair_f1: float | None = Field(default=None, ge=0, le=1)


class Experiment(StrictModel):
    schema_version: Literal[1] = 1
    input: InputConfig
    model: ModelConfig | Struct2SeqConfig | BothModelsConfig | NaMPNNConfig | AllModelsConfig = (
        Field(default_factory=ModelConfig)
    )
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)


def load_config(path: Path) -> Experiment:
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise DesignError("Configuration must be a YAML mapping")
    config = Experiment.model_validate(value)
    for key in ("sequence", "target"):
        current = getattr(config.input, key)
        if current is not None:
            setattr(config.input, key, (path.parent / current).resolve())
    config.runtime.root = (path.parent / config.runtime.root).resolve()
    if config.input.backbone:
        config.input.backbone.path = (path.parent / config.input.backbone.path).resolve()
    if config.input.reconstruction:
        for key in ("design_sequence", "mapping"):
            setattr(
                config.input.reconstruction,
                key,
                (path.parent / getattr(config.input.reconstruction, key)).resolve(),
            )
    return config
