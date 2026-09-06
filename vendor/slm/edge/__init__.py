"""Edge deployment support: ONNX export + runtimes without Python model code."""

from slm.edge.onnx_gen import OnnxLM, generate_constrained_json_onnx

__all__ = ["OnnxLM", "generate_constrained_json_onnx"]