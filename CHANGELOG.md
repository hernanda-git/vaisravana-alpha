# Changelog - vaisravana-alpha

## v0.0.34 (2026-08-01)

### Changed
- Universe ranking now uses ALL Binance futures pairs instead of hardcoded 15 pairs
- _DEFAULT_PAIRS set to empty list - universe ranker populates pairs dynamically from exchangeInfo
- runtime.py: populate settings.pairs from universe ranker on boot with fallback to 15 pairs
- All bots now use universe ranking only for pair selection

### Added
- Universe ranker dynamic pair population at runtime
- Fallback to 15 pairs if exchangeInfo fetch fails

### Fixed
- Hardcoded 15-pair limitation removed
