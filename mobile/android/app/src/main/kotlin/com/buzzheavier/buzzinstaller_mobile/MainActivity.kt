package com.buzzheavier.buzzinstaller_mobile

import android.content.Intent
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * BuzzInstaller Mobile entry point.
 *
 * Exposes BZ:// links that launched (or re-focused) the app to Dart, so the
 * Downloads page can pre-fill the link, mirroring the desktop protocol
 * handler.
 */
class MainActivity : FlutterActivity() {
    private val channelName = "buzzinstaller/intent"
    private var channel: MethodChannel? = null
    private var pendingLink: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingLink = intent?.dataString
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
        channel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "getInitialLink" -> {
                    result.success(pendingLink ?: intent?.dataString)
                    pendingLink = null
                }
                else -> result.notImplemented()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val data = intent.dataString ?: return
        pendingLink = data
        channel?.invokeMethod("link", data)
    }
}
