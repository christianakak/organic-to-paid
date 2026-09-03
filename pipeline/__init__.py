"""Signal -> claim -> cluster -> score -> brief.

Stages are separate modules because they have different costs. `claims`
spends money, `score` is free and meant to be re-run while tuning weights,
`brief` spends a little on a handful of angles.
"""
