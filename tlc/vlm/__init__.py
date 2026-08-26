"""VLM semantic layer (BUILD_BRIEF §8, spec 03 §7.9, spec 01 §7).

Five jobs only: lane count/x-positions, lane labels, annotation band extents, pencil front
presence, header text.  Nothing produced here is a measurement; positions are proposals.
This package must never import ``tlc.pipeline``.
"""
