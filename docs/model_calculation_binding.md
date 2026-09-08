# Model Scope and Calculation Lineage Binding

A final pricing, forecast, or risk estimate must declare `model_key` and
`model_scope` in its immutable final calculation node. The binding verifies the
same active registry authorization at `bound_at` and records the authorization
hash, lineage graph hash, final node ID, and deterministic binding ID. Missing
or mismatched declarations fail closed; a model authorization alone is not
evidence that it produced the recorded estimate.
