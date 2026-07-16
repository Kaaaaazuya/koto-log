# Vendored third-party assets

## chart.umd.min.js
- Library: Chart.js v4.4.0 (UMD build, minified)
- Source: https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js
- Integrity (sha256, jsdelivr official): Mh46P6mNpKqpV9EL5Xy7UU3gmJ7tj51ya10FkCzQGQQ=
- License: MIT
- 理由: 同一オリジン配信（CSP `script-src 'self'`）のため self-host する（Issue #98）。
  jsdelivr の `chart.umd.min.js` は動的生成エイリアスで SRI 非対応のため、
  正規の静的ファイル `chart.umd.js` を採用している。
