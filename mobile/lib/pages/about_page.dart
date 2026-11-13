import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/components.dart';

const _repoUrl = 'https://github.com/MapleSyrupLover/BuzzInstaller';
const _issuesUrl = '$_repoUrl/issues';
const _buzzUrl = 'https://buzzheavier.com';

class AboutPage extends StatefulWidget {
  const AboutPage({super.key});

  @override
  State<AboutPage> createState() => _AboutPageState();
}

class _AboutPageState extends State<AboutPage> {
  final BuzzApi _api = BuzzApi();
  String _version = '…';
  bool _checking = false;
  String? _status;
  ReleaseInfo? _latest;

  @override
  void initState() {
    super.initState();
    _loadVersion();
  }

  Future<void> _loadVersion() async {
    try {
      final info = await PackageInfo.fromPlatform();
      if (!mounted) return;
      setState(() {
        _version = info.version.isEmpty ? info.buildNumber : info.version;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _version = '1.0.0');
    }
  }

  Future<void> _open(String url) async {
    final uri = Uri.parse(url);
    final opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!opened && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not open $url')),
      );
    }
  }

  Future<void> _checkForUpdates() async {
    if (_checking) return;
    setState(() {
      _checking = true;
      _status = 'Checking GitHub for the latest release…';
    });
    try {
      final release = await _api.fetchLatestRelease();
      final latest = _normaliseVersion(release.tag.isEmpty ? release.name : release.tag);
      final current = _normaliseVersion(_version);
      if (!mounted) return;
      setState(() {
        _checking = false;
        _latest = release;
        _status = _isNewer(latest, current)
            ? 'Version ${release.tag} is available.'
            : 'You are on the latest desktop release.';
      });
      if (_isNewer(latest, current)) {
        await _showReleaseDialog(release);
      }
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _checking = false;
        _status = 'Could not check for updates: $error';
      });
    }
  }

  Future<void> _showReleaseDialog(ReleaseInfo release) async {
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('BuzzInstaller ${release.tag}'),
        content: SizedBox(
          width: 380,
          child: SingleChildScrollView(
            child: Text(
              release.body.trim().isEmpty
                  ? 'A newer desktop release is available.'
                  : release.body.trim(),
              style: Theme.of(dialogContext).textTheme.bodySmall,
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Close'),
          ),
          FilledButton(
            onPressed: () {
              Navigator.pop(dialogContext);
              _open(release.url.isEmpty ? '$_repoUrl/releases' : release.url);
            },
            child: const Text('Open release page'),
          ),
        ],
      ),
    );
  }

  String _normaliseVersion(String raw) {
    var text = raw.trim().toLowerCase();
    if (text.startsWith('v')) text = text.substring(1);
    return text;
  }

  bool _isNewer(String candidate, String current) {
    List<int> parse(String value) => value
        .split(RegExp(r'[.+-]'))
        .map((part) => int.tryParse(part) ?? 0)
        .toList(growable: false);
    final a = parse(candidate);
    final b = parse(current);
    for (var index = 0; index < 3; index++) {
      final left = index < a.length ? a[index] : 0;
      final right = index < b.length ? b[index] : 0;
      if (left != right) return left > right;
    }
    return false;
  }

  @override
  Widget build(BuildContext context) {
    final theme = BuzzThemeData.of(context);
    final palette = theme.palette;

    return PageBody(
      children: [
        BuzzCard(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 64,
                height: 64,
                decoration: BoxDecoration(
                  color: palette.accent,
                  borderRadius: BorderRadius.circular(theme.radius + 6),
                ),
                child: const Icon(Icons.bolt, size: 38, color: Colors.white),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'BuzzInstaller',
                      style: TextStyle(
                        fontSize: 23,
                        fontWeight: FontWeight.w800,
                        color: palette.textPrimary,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      'Mobile · version $_version',
                      style: TextStyle(
                        fontSize: 12.5,
                        color: palette.textSecondary,
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text(
                      'Upload files to BuzzHeavier and download BZ links from '
                      'your phone. Built to match the desktop app.',
                      style: TextStyle(
                        fontSize: 12.5,
                        height: 1.45,
                        color: palette.textSecondary,
                      ),
                    ),
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: const [
                        StatusPill(label: 'Android', icon: Icons.android),
                        StatusPill(label: 'Flutter', icon: Icons.flutter_dash),
                        StatusPill(label: 'Open source', icon: Icons.code),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        SectionCard(
          title: 'Updates',
          subtitle:
              'Android installs are updated manually. The app can tell you when '
              'a new desktop release exists.',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FilledButton.icon(
                onPressed: _checking ? null : _checkForUpdates,
                icon: _checking
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Icon(Icons.refresh, size: 19),
                label: Text(_checking ? 'Checking…' : 'Check for updates'),
              ),
              if (_status != null) ...[
                const SizedBox(height: 10),
                Text(
                  _status!,
                  style: TextStyle(
                    fontSize: 12,
                    color: palette.textSecondary,
                  ),
                ),
              ],
              if (_latest != null && _latest!.url.isNotEmpty) ...[
                const SizedBox(height: 4),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: () => _open(_latest!.url),
                    icon: const Icon(Icons.open_in_new, size: 16),
                    label: const Text('Open the latest release'),
                  ),
                ),
              ],
            ],
          ),
        ),
        SectionCard(
          title: 'Links',
          subtitle: 'The project is developed in the open.',
          child: Column(
            children: [
              _LinkRow(
                icon: Icons.code,
                label: 'GitHub repository',
                url: _repoUrl,
                onTap: _open,
              ),
              _LinkRow(
                icon: Icons.bug_report_outlined,
                label: 'Report an issue',
                url: _issuesUrl,
                onTap: _open,
              ),
              _LinkRow(
                icon: Icons.cloud_outlined,
                label: 'BuzzHeavier website',
                url: _buzzUrl,
                onTap: _open,
              ),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  Icons.description_outlined,
                  size: 20,
                  color: palette.textSecondary,
                ),
                title: const Text(
                  'Open-source licenses',
                  style: TextStyle(fontSize: 13.5),
                ),
                trailing: Icon(
                  Icons.chevron_right,
                  size: 19,
                  color: palette.textTertiary,
                ),
                onTap: () => showLicensePage(
                  context: context,
                  applicationName: 'BuzzInstaller Mobile',
                  applicationVersion: _version,
                ),
              ),
            ],
          ),
        ),
        BuzzCard(
          child: Text(
            'BuzzInstaller is not affiliated with BuzzHeavier. Links depend on '
            'BuzzHeavier keeping the uploaded files available.',
            style: TextStyle(
              fontSize: 11.5,
              height: 1.45,
              color: palette.textTertiary,
            ),
          ),
        ),
      ],
    );
  }
}

class _LinkRow extends StatelessWidget {
  const _LinkRow({
    required this.icon,
    required this.label,
    required this.url,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final String url;
  final Future<void> Function(String url) onTap;

  @override
  Widget build(BuildContext context) {
    final palette = BuzzThemeData.of(context).palette;
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(icon, size: 20, color: palette.textSecondary),
      title: Text(label, style: const TextStyle(fontSize: 13.5)),
      subtitle: Text(
        url,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontSize: 11, color: palette.textTertiary),
      ),
      trailing: Icon(
        Icons.open_in_new,
        size: 18,
        color: palette.textTertiary,
      ),
      onTap: () => onTap(url),
    );
  }
}
