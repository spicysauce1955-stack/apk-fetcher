# APK Fetcher

A modular, elegant, and robust APK scraper designed to download specific versions of Android applications from multiple reliable sources with automated fallback.

## 🚀 Features

- **Multi-Source Support**: Fetch APKs from APKMirror, Uptodown, and APKPure.
- **Automated Fallback**: Automatically tries sources in priority order (APKMirror -> Uptodown -> APKPure) if a download fails or a version is missing.
- **Config-Driven**: Manage your app list, versions, and source-specific metadata in a central `config.yaml`.
- **Organized Downloads**: Automatically organizes downloaded APKs by app name: `downloads/[APP_NAME]/[APK_NAME]-[VERSION]-[SOURCE].apk`.
- **Robust Error Handling**: Handles Cloudflare blocks, missing versions, and network timeouts gracefully.
- **Modular Architecture**: Easy to extend with new scraping sources.

## 📦 Project Structure

```text
apk-fetcher/
├── main.py             # CLI Entry point
├── config.yaml         # Application configuration
├── requirements.txt    # Dependencies
└── src/                # Modular scraper package
    ├── base.py         # Abstract base scraper & core logic
    ├── apkmirror.py    # APKMirror implementation
    ├── uptodown.py     # Uptodown implementation
    ├── apkpure.py      # APKPure implementation
    └── config.py       # Configuration loader
```

## 🛠️ Installation

1. **Clone the repository**:
   ```bash
   git clone <repo-url>
   cd apk-fetcher
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Install Playwright browsers**:
   ```bash
   playwright install chromium
   ```

## ⚙️ Configuration

The `config.yaml` file defines which apps to track and how to find them on each platform.

```yaml
output_dir: "./downloads"
headless: true

apps:
  telegram:
    versions: ["12.4.0"]
    sources:
      apkmirror:
        developer: "telegram-fz-llc"
        app_name: "telegram"
      uptodown:
        app_slug: "telegram"
      apkpure:
        package: "org.telegram.messenger"
```

## 📖 Usage

### Fetch all apps from config
```bash
python main.py
```

### Fetch a specific app
```bash
python main.py --app telegram
```

### Fetch a specific version
```bash
python main.py --app telegram --version 12.4.0
```

### Specify a source
```bash
python main.py --source apkmirror
```

### Bulk fetch from a JSON list
```bash
python main.py --bulk versions.json
```

### Debug Mode (See browser actions)
```bash
python main.py --no-headless --debug
```

## ⚠️ Limitations

- **APKPure**: Highly protected by Cloudflare. A mobile domain fallback (`m.apkpure.com`) is implemented which can often load versions, but the download flow itself remains heavily restricted in headless environments. Works best with APKMirror or Uptodown.
- **Dynamic Selectors**: While robust, these scrapers rely on the HTML structure of the target websites. If they change their UI, the scrapers may need updates.

## 📄 License

MIT
