import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../services/config_service.dart';
import '../theme/app_theme.dart';
import '../widgets/components.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final TextEditingController _accountController = TextEditingController();
  final TextEditingController _sizeController = TextEditingController();
  final TextEditingController _countController = TextEditingController();
  final TextEditingController _secondsController = TextEditingController();
  bool _initialised = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_initialised) return;
    _initialised = true;
    _syncFromConfig(AppConfigScope.of(context));
  }

  @override
  void dispose() {
    _accountController.dispose();
    _sizeController.dispose();
    _countController.dispose();
    _secondsController.dispose();
    super.dispose();
  }

  void _syncFromConfig(AppConfig config) {
    _accountController.text = config.accountId;
    _countController.text = '${config.uploadChunkCount}';
    _sizeController.text = config.uploadChunkSizeBytes > 0
        ? '${(config.uploadChunkSizeBytes / (1024 * 1024)).round()}'
        : '';
    _secondsController.text = '${config.clipboardClearSeconds}';
  }

  Future<void> _setMode(AppConfig config, String? mode) async {
    if (mode == null) return;
    await config.set('upload_chunk_mode', mode);
  }

  Future<void> _confirmReset(AppConfig config) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Reset all settings?'),
        content: const Text(
          'Appearance, chunking, clipboard and account settings return to '
          'their defaults. Your recent transfers are kept.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Reset'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await config.resetAll();
    _syncFromConfig(config);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Settings reset to defaults.')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final config = AppConfigScope.of(context);
    final theme = BuzzThemeData.of(context);

    return PageBody(
      children: [
        SectionCard(
          title: 'Upload chunking',
          subtitle:
              'Default plan used by the Uploads page. Every upload can still '
              'override this from the chunk menu.',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              DropdownButtonFormField<String>(
                key: ValueKey('mode-${config.uploadChunkMode}'),
                initialValue: config.uploadChunkMode,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Default plan'),
                items: const [
                  DropdownMenuItem(
                    value: 'auto',
                    child: Text('Automatic (single upload)'),
                  ),
                  DropdownMenuItem(
                    value: 'ask',
                    child: Text('Ask before every upload'),
                  ),
                  DropdownMenuItem(
                    value: 'count',
                    child: Text('Fixed number of parts'),
                  ),
                  DropdownMenuItem(
                    value: 'custom',
                    child: Text('Fixed part size'),
                  ),
                ],
                onChanged: (mode) => _setMode(config, mode),
              ),
              if (config.uploadChunkMode == 'count') ...[
                const SizedBox(height: 14),
                Text(
                  'Parts per file',
                  style: TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w600,
                    color: theme.palette.textSecondary,
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  children: [
                    for (final preset in const [2, 4, 8])
                      ChoiceChip(
                        label: Text('$preset'),
                        selected: config.uploadChunkCount == preset,
                        onSelected: (_) => config.set(
                          'upload_chunk_count',
                          preset,
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 10),
                TextField(
                  controller: _countController,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  decoration: const InputDecoration(
                    labelText: 'Custom number of parts',
                    helperText: 'Between 2 and 512',
                  ),
                  onSubmitted: (value) {
                    final parsed = int.tryParse(value);
                    if (parsed == null) return;
                    config.set('upload_chunk_count', parsed.clamp(2, 512));
                  },
                ),
              ],
              if (config.uploadChunkMode == 'custom') ...[
                const SizedBox(height: 14),
                TextField(
                  controller: _sizeController,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  decoration: const InputDecoration(
                    labelText: 'Part size (MiB)',
                    helperText: 'Minimum 1 MiB per part',
                  ),
                  onSubmitted: (value) {
                    final mib = int.tryParse(value);
                    if (mib == null || mib < 1) return;
                    config.set(
                      'upload_chunk_size_bytes',
                      mib * 1024 * 1024,
                    );
                  },
                ),
              ],
            ],
          ),
        ),
        SectionCard(
          title: 'BuzzHeavier account',
          subtitle:
              'Optional. With an account ID, uploads are attached to your '
              'BuzzHeavier account instead of being anonymous.',
          child: TextField(
            controller: _accountController,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'Account ID',
              hintText: 'Paste your BuzzHeavier account ID',
            ),
            onChanged: (value) =>
                config.set('buzzheavier_account_id', value.trim()),
          ),
        ),
        SectionCard(
          title: 'Clipboard',
          subtitle:
              'Uploads copy their BZ link automatically. Control how long the '
              'link stays on the clipboard.',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              DropdownButtonFormField<String>(
                key: ValueKey('clip-${config.clipboardClearMode}'),
                initialValue: config.clipboardClearMode,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Clear copied link'),
                items: const [
                  DropdownMenuItem(
                    value: 'never',
                    child: Text('Keep until I copy again'),
                  ),
                  DropdownMenuItem(
                    value: 'timeout',
                    child: Text('Clear after a delay'),
                  ),
                ],
                onChanged: (mode) {
                  if (mode == null) return;
                  config.set('auto_copy_clear_mode', mode);
                },
              ),
              if (config.clipboardClearMode == 'timeout') ...[
                const SizedBox(height: 14),
                TextField(
                  controller: _secondsController,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  decoration: const InputDecoration(
                    labelText: 'Delay (seconds)',
                    helperText: 'Between 2 and 600 seconds',
                  ),
                  onSubmitted: (value) {
                    final seconds = int.tryParse(value);
                    if (seconds == null) return;
                    config.set(
                      'auto_copy_clear_seconds',
                      seconds.clamp(2, 600),
                    );
                  },
                ),
              ],
            ],
          ),
        ),
        SectionCard(
          title: 'Behaviour',
          subtitle: 'How the app reacts while you use it.',
          child: Column(
            children: [
              SwitchListTile.adaptive(
                contentPadding: EdgeInsets.zero,
                title: const Text(
                  'Completion messages',
                  style: TextStyle(fontSize: 13.5),
                ),
                subtitle: Text(
                  'Show a banner when an upload or download finishes.',
                  style: TextStyle(
                    fontSize: 11.5,
                    color: theme.palette.textSecondary,
                  ),
                ),
                value: config.notificationsEnabled,
                onChanged: (value) =>
                    config.set('notifications_enabled', value),
              ),
              SwitchListTile.adaptive(
                contentPadding: EdgeInsets.zero,
                title: const Text(
                  'Ask before uploading',
                  style: TextStyle(fontSize: 13.5),
                ),
                subtitle: Text(
                  'Show a confirmation sheet listing files and the chunk plan.',
                  style: TextStyle(
                    fontSize: 11.5,
                    color: theme.palette.textSecondary,
                  ),
                ),
                value: config.boolValue('confirm_before_upload'),
                onChanged: (value) =>
                    config.set('confirm_before_upload', value),
              ),
            ],
          ),
        ),
        BuzzCard(
          child: Row(
            children: [
              Expanded(
                child: Text(
                  'Reset settings',
                  style: TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                    color: theme.palette.textPrimary,
                  ),
                ),
              ),
              OutlinedButton(
                onPressed: () => _confirmReset(config),
                child: const Text('Reset'),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
