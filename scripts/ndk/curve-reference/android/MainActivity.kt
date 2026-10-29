package com.visioncamhealth

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.hardware.display.DisplayManager
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings
import android.view.OrientationEventListener
import android.view.Surface
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.statusBarsPadding
import android.app.Activity
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowInsetsControllerCompat
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.res.stringResource
import androidx.compose.material.icons.filled.Info
import kotlinx.coroutines.launch
import androidx.compose.material3.PlainTooltip
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearOutSlowInEasing
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.visioncamhealth.camera.CameraStreamer
import com.visioncamhealth.camera.ConnectionState
import com.visioncamhealth.camera.StreamMode
import com.visioncamhealth.service.MetricsService
import com.visioncamhealth.zyre.ZreService
import kotlin.math.roundToInt
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import java.net.URI

/**
 * Port the device publishes on when the endpoint is left at its default.
 *
 * This is the DEVICE's own port, not the gateway's. The gateway binds 5556 for
 * its SUB socket (zmq_server.py --bind), so a phone publishing to 5556 would
 * collide with it when both run on the same host. 15556 keeps them apart.
 */
private const val DEFAULT_STREAM_PORT = 15556

/** Where direct publishing landed before the split above. */
private const val LEGACY_STREAM_PORT = 5556

/**
 * Where the phone should send a stream when the user has not said.
 *
 * This phone's own LAN address, not a stand-in like 192.168.1.100. A default
 * that cannot possibly work is worse than none: it looks configured, so nobody
 * thinks to change it, and the stream silently goes nowhere while the UI says
 * Live. Pointing at the device's own address is at least real, and for a
 * gateway running on the same machine as the phone it is often correct.
 *
 * Falls back to the emulator's host loopback, the only address guaranteed to
 * exist when no LAN interface does.
 */
private fun defaultGatewayEndpoint(): String {
    val host = localLanAddress() ?: "10.0.2.2"
    return "tcp://$host:$DEFAULT_STREAM_PORT"
}

/**
 * Z85 alphabet (ZeroMQ RFC 32). The mesh key fields only accept these
 * characters and 40 of them; native zmq_z85_decode keeps the final say
 * on a pair, but pasting anything outside this alphabet is a typo and
 * gets filtered at the field instead of failing opaque at create().
 */
private const val Z85_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz" +
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ" +
    ".-:+=^!/*?&<>()[]{}@%$#"

private fun filterZ85(raw: String): String =
    raw.take(40).filter { it in Z85_CHARS }

private const val EXPOSURE_DRAG_DENSITY = 400f
private const val FOCUS_RING_DURATION_MS = 1200L
private const val EV_HUD_DURATION_MS = 1500L

private enum class FocusMode(@androidx.annotation.StringRes val label: Int) {
    AUTO(R.string.focus_auto),
    MANUAL(R.string.focus_manual)
}

// AppCompatActivity rather than ComponentActivity: AppCompatActivity is what
// carries per-app locale support below API 33. On 33+ the platform LocaleManager
// does it, but on 24..32 AppCompat is the only thing that can back
// setApplicationLocales(). It is a ComponentActivity underneath, so nothing
// else about the activity changes.
class MainActivity : androidx.appcompat.app.AppCompatActivity() {

    private lateinit var cameraStreamer: CameraStreamer

    private var isRunning by mutableStateOf(false)
    private var notificationGranted by mutableStateOf(false)
    private var batteryOptGranted by mutableStateOf(false)
    private var locationGranted by mutableStateOf(false)
    private var cameraGranted by mutableStateOf(false)
    private var micGranted by mutableStateOf(false)

    private var currentConnection: ServiceConnection? = null

    // ── Permission launchers ──────────────────────────────────────────

    private val notifLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        notificationGranted = granted
    }

    private val locationLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        locationGranted = granted
    }

    private val cameraLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        cameraGranted = granted
        if (granted && !cameraStreamer.isPreviewRunning) {
            cameraStreamer.startPreview()
        }
    }

    private val batteryOptLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        batteryOptGranted = isIgnoringBatteryOptimizations(this)
    }

    private val micLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        micGranted = granted
    }

    // ── Lifecycle ─────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            // Do NOT let it auto-detect. The default SystemBarStyle picks bar
            // icon colours from the SYSTEM dark theme, which has nothing to do
            // with what is on screen here: the viewfinder is always black, so
            // in light mode Android handed us dark icons on a black background
            // and the clock, signal and battery simply vanished.
            //
            // dark(scrim) means LIGHT icons. These are the viewfinder defaults;
            // the LaunchedEffect below flips them to dark icons while the
            // light settings sheet is open.
            statusBarStyle = androidx.activity.SystemBarStyle.dark(
                android.graphics.Color.TRANSPARENT
            ),
            navigationBarStyle = androidx.activity.SystemBarStyle.dark(
                android.graphics.Color.TRANSPARENT
            )
        )

        cameraStreamer = CameraStreamer(this)
        getSystemService(DisplayManager::class.java).registerDisplayListener(displayListener, null)

        // DEBUG lab hook (never in release): the app itself starts the
        // zre discovery service with the DEBUG rendezvous endpoint —
        // the shell/tool-driven am path cannot start a non-exported
        // foreground service with extras, and the in-app path is the
        // only honest way to exercise the gossip connect from the app
        // uid. The boot path (BootReceiver) carries no extras.
        if (BuildConfig.DEBUG && BuildConfig.ZRE_DEBUG_GOSSIP.isNotBlank()) {
            startService(
                Intent(this, ZreService::class.java).apply {
                    action = ZreService.ACTION_START
                    putExtra(ZreService.EXTRA_GOSSIP, BuildConfig.ZRE_DEBUG_GOSSIP)
                }
            )
        }

        if (orientationListener.canDetectOrientation()) orientationListener.enable()

        notificationGranted = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        batteryOptGranted = isIgnoringBatteryOptimizations(this)
        locationGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        cameraGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

        // ISOLATION hook: `--ez encoder_only true` isolates the encoder
        // surface from the viewfinder to locate encoder-only artifacts.
        if (intent?.getBooleanExtra("encoder_only", false) == true) {
            cameraStreamer.encoderOnlyDebug = true
        }

        if (cameraGranted) {
            cameraStreamer.startPreview()
        } else if (getSharedPreferences("visioncam", MODE_PRIVATE)
                .getBoolean("onboarding_complete", false)
        ) {
            // Existing installs are asked straight away, as before. On a first
            // run the onboarding screen asks instead -- popping a system
            // dialog before the app has drawn anything means the user is asked
            // for the camera before they have seen what they are granting it
            // to, or picked a language to read it in.
            cameraLauncher.launch(Manifest.permission.CAMERA)
        }

        setContent {
            val context = LocalContext.current
            val colorScheme = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                if (isSystemInDarkTheme()) dynamicDarkColorScheme(context)
                else dynamicLightColorScheme(context)
            } else {
                if (isSystemInDarkTheme()) dynamicDarkColorScheme(context)
                else dynamicLightColorScheme(context)
            }

            MaterialTheme(colorScheme = colorScheme) {
                val prefs = context.getSharedPreferences("visioncam", MODE_PRIVATE)

                // Onboarding is shown once. Read into state rather than a val so
                // that finishing it dismisses the screen without a restart,
                // instead of waiting for the next launch to notice.
                var showOnboarding by remember {
                    mutableStateOf(!prefs.getBoolean("onboarding_complete", false))
                }


                var streamUrl by remember {
                    mutableStateOf(
                        prefs.getString("zmq_endpoint", null)
                            ?: prefs.getString("stream_url", null)
                                ?.takeIf { it.startsWith("tcp://") }
                            ?: defaultGatewayEndpoint()
                    )
                }

                // ── Mesh discovery identity (Curve pair) ──────────────
                // The operator pastes the pair here; the service reads the
                // same prefs (ZreService.PREFS_ID). No key generation lives
                // in the app — the pair comes from the dashboard flow.
                val zrePrefs = context.getSharedPreferences(
                    ZreService.PREFS_ID, MODE_PRIVATE)
                var meshPub by remember {
                    mutableStateOf(zrePrefs.getString(ZreService.PREF_KEY_PUB, "") ?: "")
                }
                var meshPriv by remember {
                    mutableStateOf(zrePrefs.getString(ZreService.PREF_KEY_SEC, "") ?: "")
                }
                var meshPrivReveal by remember { mutableStateOf(false) }
                fun applyMeshIdentity() {
                    // Both 40-char fields = a complete pair. The save
                    // rebuilds the running node so the operator's paste
                    // takes effect immediately (also the compromise path:
                    // replace the pair, the rebuild rotates the identity).
                    if (meshPub.length == 40 && meshPriv.length == 40) {
                        zrePrefs.edit()
                            .putString(ZreService.PREF_KEY_PUB, meshPub)
                            .putString(ZreService.PREF_KEY_SEC, meshPriv)
                            .apply()
                        startService(
                            Intent(this@MainActivity, ZreService::class.java).apply {
                                action = ZreService.ACTION_REKEY
                            }
                        )
                    }
                }

                var connectionState by remember {
                    mutableStateOf<ConnectionState>(ConnectionState.Idle)
                }
                // Publish topology. "direct" = the phone binds its own PUB
                // socket and viewers connect to tcp://<phone-ip>:<port>, with
                // no gateway process involved. "server" = the phone connects
                // out to a gateway that re-publishes, which is what survives
                // AP client isolation.
                var directPublish by remember {
                    mutableStateOf(prefs.getBoolean("direct_publish", false))
                }
                // In direct mode the field carries this phone's own LAN
                // address, not a bind wildcard: it is the address a viewer
                // needs, and it is re-resolved at start so a DHCP change
                // rewrites it instead of leaving a stale bind.
                if (directPublish) {
                    val resolved = resolveDirectEndpoint(
                        streamUrl, prefs.getString("last_lan_ip", null))
                    if (resolved != streamUrl) {
                        streamUrl = resolved
                        // Persist too, so a restart does not resurrect the
                        // stale address for even one launch.
                        prefs.edit().putString("zmq_endpoint", resolved).apply()
                    }
                    localLanAddress()?.let { prefs.edit().putString("last_lan_ip", it).apply() }
                }
                // The endpoint the app auto-resolved for Direct mode. Kept so
                // a later switch to Direct can tell "the user typed this" from
                // "we wrote this", and only the former is worth saving as a
                // gateway.
                var autoDirectEndpoint by remember { mutableStateOf("") }

                var showSettings by remember { mutableStateOf(false) }

                // Status bar icons follow the surface actually behind them.
                //
                // Light on the black viewfinder, dark on the light settings
                // sheet and the light onboarding page. This used to be pinned
                // light for the whole app, on the reasoning that the settings
                // sheet is a bottom sheet that stops below the bar so the
                // viewfinder is always behind it. Onboarding breaks that
                // reasoning: it is a full light page that does go behind the
                // bar, and light icons on it were close to invisible.
                //
                // Keyed on both, so it settles correctly whichever screen is up.
                val lightSurface = showSettings || showOnboarding
                val view = LocalView.current
                if (!view.isInEditMode) {
                    LaunchedEffect(lightSurface) {
                        val window = (view.context as? Activity)?.window ?: return@LaunchedEffect
                        WindowInsetsControllerCompat(window, view).apply {
                            isAppearanceLightStatusBars = lightSurface
                            isAppearanceLightNavigationBars = lightSurface
                        }
                    }

                if (showOnboarding) {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .background(MaterialTheme.colorScheme.surface),
                        contentAlignment = Alignment.TopCenter,
                    ) {
                        com.visioncamhealth.ui.OnboardingScreen(
                            cameraGranted = cameraGranted,
                            micGranted = micGranted,
                            notificationGranted = notificationGranted,
                            locationGranted = locationGranted,
                            batteryOptGranted = batteryOptGranted,
                            onRequestCamera = { cameraLauncher.launch(Manifest.permission.CAMERA) },
                            onRequestMic = { micLauncher.launch(Manifest.permission.RECORD_AUDIO) },
                            onRequestNotification = { notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) },
                            onRequestLocation = { locationLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION) },
                            onRequestBattery = { batteryOptLauncher.launch(batteryOptIntent()) },
                            onFinish = {
                                prefs.edit().putBoolean("onboarding_complete", true).apply()
                                showOnboarding = false
                            },
                        )
                    }
                    return@MaterialTheme
                }
                }
                var streamUptimeSeconds by remember { mutableLongStateOf(0L) }
                var hostName by remember { mutableStateOf("") }
                var focusPoint by remember { mutableStateOf<Offset?>(null) }
                var evFraction by remember { mutableFloatStateOf(0f) }
                var exposureEv by remember { mutableIntStateOf(0) }
                var showEvHud by remember { mutableStateOf(false) }
                var focusMode by remember { mutableStateOf(FocusMode.AUTO) }
                var streamMode by remember { mutableStateOf(StreamMode.STREAMING) }

                LaunchedEffect(focusPoint) {
                    if (focusPoint != null) {
                        delay(FOCUS_RING_DURATION_MS)
                        focusPoint = null
                    }
                }

                LaunchedEffect(exposureEv) {
                    if (showEvHud) {
                        delay(EV_HUD_DURATION_MS)
                        showEvHud = false
                    }
                }

                // Wire camera streamer callbacks to Compose state
                LaunchedEffect(Unit) {
                    cameraStreamer.onConnectionStateChanged = { state ->
                        connectionState = state
                    }
                }

                // Uptime ticker – runs while connected
                LaunchedEffect(connectionState) {
                    if (connectionState is ConnectionState.Connected) {
                        streamUptimeSeconds = 0L
                        val startTime = System.currentTimeMillis()
                        while (connectionState is ConnectionState.Connected && isActive) {
                            streamUptimeSeconds = (System.currentTimeMillis() - startTime) / 1000
                            delay(1000)
                        }
                    } else {
                        streamUptimeSeconds = 0L
                    }
                }

                // ── Layout: full-screen camera + overlay ──────────────

                Box(modifier = Modifier.fillMaxSize()) {

                    // Layer 1: Camera preview — laid out at the live stream
                    // display aspect (reactive, defaults to portrait 9:16).
                    val previewSize by cameraStreamer.previewDisplay
                    CameraPreviewOverlay(
                        cameraGranted = cameraGranted,
                        onRequestCamera = { cameraLauncher.launch(Manifest.permission.CAMERA) },
                        previewView = cameraStreamer.previewView,
                        displayW = previewSize.first,
                        displayH = previewSize.second
                    )

                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        Box(Modifier.fillMaxSize().background(
                            Brush.verticalGradient(listOf(
                                Color.Black.copy(alpha = 0.55f),
                                Color.Transparent,
                                Color.Transparent,
                                Color.Black.copy(alpha = 0.55f)
                            ))
                        ).pointerInput(Unit) {
                            detectTapGestures { offset ->
                                cameraStreamer.tapToFocus(offset.x, offset.y)
                                focusPoint = offset
                            }
                        }.pointerInput(Unit) {
                            detectTransformGestures { _, pan, zoom, _ ->
                                if (zoom != 1f) {
                                    cameraStreamer.setZoom(cameraStreamer.getZoom() * zoom)
                                } else if (pan.y != 0f) {
                                    val maxEv = cameraStreamer.getMaxExposure()
                                    if (maxEv > 0) {
                                        evFraction = (evFraction - pan.y / EXPOSURE_DRAG_DENSITY).coerceIn(-maxEv.toFloat(), maxEv.toFloat())
                                        exposureEv = evFraction.roundToInt()
                                        cameraStreamer.setExposure(exposureEv)
                                        showEvHud = true
                                    }
                                }
                            }
                        }) {
                            focusPoint?.let { p ->
                                Canvas(Modifier.fillMaxSize()) {
                                    drawCircle(
                                        color = Color.White,
                                        radius = 28.dp.toPx(),
                                        center = p,
                                        style = Stroke(width = 2.dp.toPx())
                                    )
                                    drawCircle(
                                        color = Color.White.copy(alpha = 0.6f),
                                        radius = 44.dp.toPx(),
                                        center = p,
                                        style = Stroke(width = 1.dp.toPx())
                                    )
                                }
                            }
                        }

                        if (showEvHud) {
                            Text(
                                text = stringResource(
                    R.string.ev_value,
                    (if (exposureEv > 0) "+" else "") + exposureEv
                ),
                                style = MaterialTheme.typography.titleMedium,
                                color = Color.White,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier
                                    .align(Alignment.TopCenter)
                                    .padding(top = 16.dp)
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(Color.Black.copy(alpha = 0.5f))
                                    .padding(horizontal = 14.dp, vertical = 6.dp)
                            )
                        }

                            Row(
                                Modifier
                                    .align(Alignment.TopStart)
                                    .fillMaxWidth()
                                    // Clear the system status bar: the clock
                                    // and signal icons live up there and this
                                    // row would otherwise sit on top of them.
                                    .statusBarsPadding()
                                    .padding(start = 20.dp, end = 20.dp, top = 12.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                // Liveness, from REAL published frames.
                                // Keying the animation on the raw counter
                                // restarted it every frame (~33ms), which
                                // shimmers; a frame bucket gives one clean
                                // beat per cycle. It also FREEZES when frames
                                // stop, because the bucket stops moving.
                                val pulseCounter by cameraStreamer.streamPulse
                                    .collectAsState()
                                StreamStatusRow(
                                    connectionState, hostName, streamUptimeSeconds,
                                    pulseBucket = pulseCounter / PULSE_FRAMES)
                                if (connectionState is ConnectionState.Connected ||
                                    connectionState is ConnectionState.Connecting ||
                                    connectionState is ConnectionState.Reconnecting
                                ) {
                                    Text(
                                        formatDuration(streamUptimeSeconds),
                                        style = MaterialTheme.typography.titleMedium,
                                        color = Color.White,
                                        fontWeight = FontWeight.SemiBold,
                                        modifier = Modifier
                                            .clip(RoundedCornerShape(12.dp))
                                            .background(Color.Black.copy(alpha = 0.45f))
                                            .padding(horizontal = 12.dp, vertical = 4.dp)
                                    )
                                }
                            }

                            Row(
                                Modifier
                                    .align(Alignment.BottomCenter)
                                    .fillMaxWidth()
                                    .padding(start = 28.dp, end = 28.dp, bottom = 44.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                // Settings sits on the same line as the
                                // play/stop button and is anchored left, so
                                // the transport control stays dead centre.
                                //
                                // 44dp from the bottom puts the 72dp button
                                // centred in the band between the end of the
                                // viewfinder and the system gesture bar, rather
                                // than overlapping the picture above and
                                // sitting in the nav area below.
                                IconButton(
                                    onClick = { showSettings = true },
                                    modifier = Modifier.size(48.dp)
                                ) {
                                    Icon(
                                        Icons.Default.Settings,
                        stringResource(R.string.cd_settings),
                                        tint = Color.White,
                                        modifier = Modifier.size(24.dp)
                                    )
                                }
                                Spacer(Modifier.weight(1f))
                                val showStop =
                                    connectionState is ConnectionState.Connected ||
                                        connectionState is ConnectionState.Connecting ||
                                        connectionState is ConnectionState.Reconnecting

                                FilledIconButton(
                                    onClick = {
                                        if (showStop) {
                                            cameraStreamer.stopStream()
                                            stopMetricsService()
                                        } else {
                                            // Re-resolve right before binding so the socket never gets a stale
                                            // address after a DHCP change.
                                            val effective = if (directPublish)
                                                resolveDirectEndpoint(
                                                    streamUrl,
                                                    prefs.getString("last_lan_ip", null))
                                            else streamUrl
                                            if (directPublish) {
                                                localLanAddress()?.let {
                                                    prefs.edit().putString("last_lan_ip", it).apply()
                                                }
                                            }
                                            if (effective != streamUrl) streamUrl = effective
                                            prefs.edit().putString("zmq_endpoint", effective).apply()
                                            hostName = extractHost(effective)
                                            cameraStreamer.startStream(
                                                effective,
                                                directBind = directPublish)
                                            // STREAMING sends muxed A+V only —
                                            // metrics would land in the same
                                            // byte stream FFmpeg demuxes and
                                            // break it. TRAFFIC carries both.
                                            if (streamMode == StreamMode.TRAFFIC &&
                                                !isRunning && notificationGranted
                                            ) {
                                                isRunning = true
                                                startMetricsService()
                                            }
                                        }
                                    },
                                    modifier = Modifier.size(72.dp),
                                    colors = IconButtonDefaults.filledIconButtonColors(
                                        containerColor = if (showStop)
                                            MaterialTheme.colorScheme.error
                                        else MaterialTheme.colorScheme.primary
                                    )
                                ) {
                                    Icon(
                                        if (showStop) Icons.Default.Stop else Icons.Default.PlayArrow,
                                        if (showStop) stringResource(R.string.cd_stop_stream) else stringResource(R.string.cd_start_stream),
                                        tint = Color.White, modifier = Modifier.size(36.dp)
                                    )
                                }

                                // Mirror the settings button on the right so
                                // the play/stop control is exactly centred.
                                Spacer(Modifier.weight(1f))
                                Spacer(Modifier.size(48.dp))
                            }
                        }
                    }

                SettingsSheetOverlay(
                    showSettings = showSettings,
                    streamUrl = streamUrl,
                    onEndpointChange = { newStreamUrl ->
                        val endpointChanged = newStreamUrl != streamUrl
                        streamUrl = newStreamUrl
                        prefs.edit().putString("zmq_endpoint", newStreamUrl).apply()
                        // Only restart telemetry when the endpoint actually
                        // moved. onEndpointChange fires per keystroke, and
                        // restarting the service on every character would
                        // thrash it while someone is still typing an address.
                        if (endpointChanged && isRunning) startMetricsService(newStreamUrl)
                    },
                    streamMode = streamMode,
                    onStreamModeChange = { mode ->
                        streamMode = mode
                        cameraStreamer.streamMode = mode
                        if (mode.audio && !micGranted) {
                            micLauncher.launch(Manifest.permission.RECORD_AUDIO)
                        }
                        // Metrics run in TRAFFIC mode only; switch live so the
                        // telemetry service matches what is actually sent.
                        if (mode == StreamMode.TRAFFIC && cameraStreamer.isStreaming) {
                            if (!isRunning && notificationGranted) {
                                isRunning = true
                                startMetricsService()
                            }
                        } else if (mode == StreamMode.STREAMING && isRunning) {
                            stopMetricsService()
                        }
                    },
                    directPublish = directPublish,
                    onDirectPublishChange = { direct ->
                        directPublish = direct
                        prefs.edit().putBoolean("direct_publish", direct).apply()
                        if (direct) {
                            // Before overwriting: keep whatever gateway the
                            // user had set up. Direct mode replaces the field
                            // with this phone's own address, so without this the
                            // configured gateway is simply lost the first time
                            // they try Direct mode.
                            //
                            // lastAutoDirectEndpoint is the value WE wrote last
                            // time. Anything else in the field is the user's,
                            // and is worth saving even if it looks odd -- a
                            // wrong gateway is still their gateway, and they
                            // may have corrected it here.
                            if (streamUrl.isNotBlank() && streamUrl != autoDirectEndpoint) {
                                prefs.edit()
                                    .putString(KEY_LAST_GATEWAY, streamUrl)
                                    .apply()
                            }

                            val port = streamUrl.substringAfterLast(':').ifBlank { "5560" }
                            val resolved = resolveDirectEndpoint("tcp://*:$port")
                            if (resolved.startsWith("tcp://")) {
                                streamUrl = resolved
                                autoDirectEndpoint = resolved
                                prefs.edit().putString("zmq_endpoint", resolved).apply()
                                localLanAddress()?.let {
                                    prefs.edit().putString("last_lan_ip", it).apply()
                                }
                            }
                        } else {
                            // Switching back to Server: restore the gateway
                            // they configured. Without this the field kept the
                            // phone's own direct address, so the phone
                            // connected to itself and no gateway ever received
                            // anything -- which looked exactly like a broken
                            // gateway.
                            val remembered = prefs.getString(KEY_LAST_GATEWAY, null)
                            if (!remembered.isNullOrBlank() && remembered != streamUrl) {
                                streamUrl = remembered
                                prefs.edit().putString("zmq_endpoint", remembered).apply()
                            }
                        }
                    },
                    focusMode = focusMode,
                    onFocusModeChange = { mode ->
                        focusMode = mode
                        if (mode == FocusMode.AUTO) {
                            cameraStreamer.enableAutoFocus()
                        } else {
                            cameraStreamer.disableAutoFocus()
                        }
                    },
                    meshPub = meshPub,
                    meshPriv = meshPriv,
                    meshPrivReveal = meshPrivReveal,
                    onMeshPubChange = { raw ->
                        meshPub = filterZ85(raw)
                        applyMeshIdentity()
                    },
                    onMeshPrivChange = { raw ->
                        meshPriv = filterZ85(raw)
                        applyMeshIdentity()
                    },
                    onMeshPrivRevealToggle = { meshPrivReveal = !meshPrivReveal },
                    isMetricsRunning = isRunning,
                    notificationGranted = notificationGranted,
                    locationGranted = locationGranted,
                    batteryOptGranted = batteryOptGranted,
                    onRequestNotification = {
                        notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                    },
                    onRequestLocation = {
                        locationLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION)
                    },
                    onRequestBattery = { batteryOptLauncher.launch(batteryOptIntent()) },
                    onDismiss = { showSettings = false }
                )
            }
        }
    }

    /**
     * The battery-optimisation exemption request.
     *
     * Built in one place because both onboarding and the settings sheet ask for
     * it, and a request that differs between the two would leave whichever
     * screen was not updated asking again forever.
     */
    private fun batteryOptIntent(): Intent =
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
            data = android.net.Uri.parse("package:$packageName")
        }

    private fun currentRotation(): Int {
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                display?.rotation ?: Surface.ROTATION_0
            } else {
                getSystemService(DisplayManager::class.java)
                    ?.getDisplay(android.view.Display.DEFAULT_DISPLAY)
                    ?.rotation ?: Surface.ROTATION_0
            }
        } catch (_: Exception) {
            Surface.ROTATION_0
        }
    }

    /**
     * Physical attitude of the device, tracked independently of the display.
     *
     * The app is a fixed portrait control surface and never rotates, so the
     * display is pinned to ROTATION_0 for its whole life and can never say
     * anything about how the camera is mounted. The stream's frame has to
     * follow the CAMERA, not the window, and the accelerometer is the only
     * thing that knows which way the camera is pointing. Reported as
     * 0 / 90 / 180 / 270 and consumed only by the streamer's orientation lock;
     * nothing here moves the UI.
     */
    private val orientationListener by lazy {
        object : OrientationEventListener(this) {
            override fun onOrientationChanged(orientation: Int) {
                if (orientation == ORIENTATION_UNKNOWN) return
                val deg = when {
                    orientation > 315 || orientation <= 45 -> 0
                    orientation <= 135 -> 90
                    orientation <= 225 -> 180
                    else -> 270
                }
                cameraStreamer.setPhysicalOrientation(deg)
            }
        }
    }

    private val displayListener = object : DisplayManager.DisplayListener {
        override fun onDisplayChanged(displayId: Int) {
            val rotation = currentRotation()
            cameraStreamer.handleOrientationChange(
                when (rotation) {
                    Surface.ROTATION_90 -> 90
                    Surface.ROTATION_180 -> 180
                    Surface.ROTATION_270 -> 270
                    else -> 0
                }
            )
        }

        override fun onDisplayAdded(displayId: Int) {}
        override fun onDisplayRemoved(displayId: Int) {}
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        val rotation = currentRotation()
        val displayDeg = when (rotation) {
            Surface.ROTATION_90 -> 90
            Surface.ROTATION_180 -> 180
            Surface.ROTATION_270 -> 270
            else -> 0
        }
        cameraStreamer.handleOrientationChange(displayDeg)
    }

    override fun onDestroy() {
        getSystemService(DisplayManager::class.java).unregisterDisplayListener(displayListener)
        orientationListener.disable()
        cameraStreamer.release()
        stopMetricsService()
        super.onDestroy()
    }

    // ── MetricsService ────────────────────────────────────────────────

    private fun stopMetricsService() {
        stopService(Intent(this, MetricsService::class.java).apply {
            action = MetricsService.ACTION_STOP
        })
        currentConnection?.let { conn ->
            try { unbindService(conn) } catch (_: Exception) {}
        }
        currentConnection = null
        isRunning = false
    }

    private fun startMetricsService(endpoint: String? = null) {
        val prefs = getSharedPreferences("visioncam", MODE_PRIVATE)
        val url = endpoint
            ?: prefs.getString("zmq_endpoint", MetricsService.DEFAULT_ZMQ_ENDPOINT)
            ?: MetricsService.DEFAULT_ZMQ_ENDPOINT

        val intent = Intent(this, MetricsService::class.java).apply {
            action = MetricsService.ACTION_START
            putExtra(MetricsService.EXTRA_ZMQ_ENDPOINT, url)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        val conn = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName?, service: IBinder?) {}
            override fun onServiceDisconnected(name: ComponentName?) {
                isRunning = false
            }
        }
        currentConnection?.let { old ->
            try {
                unbindService(old)
            } catch (_: Exception) {}
        }
        currentConnection = conn
        bindService(intent, conn, Context.BIND_AUTO_CREATE)
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Private helpers (top-level, activity-adjacent)
// ═══════════════════════════════════════════════════════════════════════

private fun isIgnoringBatteryOptimizations(context: Context): Boolean {
    val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return false
    return pm.isIgnoringBatteryOptimizations(context.packageName)
}

/**
 * This phone's LAN IPv4 address, or null if there isn't one.
 *
 * In direct-publish mode the socket binds a WILDCARD host (an asterisk), which
 * is the correct thing to bind -- it accepts on every interface and so
 * survives the phone switching between Wi-Fi and a hotspot. But a wildcard is
 * useless to type into a player, so the concrete address is resolved here
 * purely for DISPLAY.
 *
 * NB: a literal endpoint ending in "asterisk:port" must NOT be written in a
 * KDoc block. Kotlin nests block comments, and the slash-asterisk in such a
 * URL opens a nested comment that is never closed, silently commenting out
 * the rest of the file.
 *
 * Deliberately uses NetworkInterface rather than WifiManager: enumerating
 * interfaces needs no permission and does not force the Wi-Fi radio on, so it
 * still answers correctly when the phone is sharing a hotspot instead.
 */
private fun localLanAddress(): String? = try {
    val candidates = java.net.NetworkInterface.getNetworkInterfaces().toList()
        .filter { runCatching { it.isUp && !it.isLoopback }.getOrDefault(false) }
        .mapNotNull { nic ->
            val addrs = runCatching { nic.inetAddresses.toList() }.getOrDefault(emptyList())
                .filterIsInstance<java.net.Inet4Address>()
                .mapNotNull { it.hostAddress }
                // 127.x is loopback and 169.254.x is an APIPA fallback that
                // means "DHCP failed" -- neither is usable as a viewer address.
                .filter { h -> !h.startsWith("127.") && !h.startsWith("169.254.") }
            if (addrs.isEmpty()) null else (nic.name to addrs)
        }
    // Prefer the real LAN interfaces by name. Otherwise a VPN or tunnelling
    // interface can win the enumeration and hand someone an address that
    // only resolves on this device -- the classic "it works on the phone but
    // not from the laptop" failure. This is the PRIVATE address, never the
    // public one: nothing here talks to the internet.
    val preferred = listOf("wlan0", "wlan1", "eth0", "en0")
    candidates.firstOrNull { (name, _) -> name in preferred }?.second?.first()
        ?: candidates.flatMap { it.second }.firstOrNull()
} catch (_: Exception) {
    null
}

/**
 * The endpoint with this phone's own LAN address substituted for a wildcard.
 *
 * Re-resolved every time it is shown and again at start, so a DHCP change
 * rewrites a stale address instead of leaving the socket bound to an IP the
 * phone no longer has. If the host is something else entirely -- a gateway in
 * server mode -- it is left untouched.
 */
private fun resolveDirectEndpoint(configured: String, lastKnownIp: String? = null): String {
    val trimmed = configured.trim()
    val schemeEnd = trimmed.indexOf("://")
    if (schemeEnd < 0) return configured
    val hostStart = schemeEnd + 3
    val colon = trimmed.lastIndexOf(':')
    val hostEnd = if (colon > hostStart &&
        trimmed.substring(colon + 1).all { it.isDigit() }
    ) colon else trimmed.length
    val host = trimmed.substring(hostStart, hostEnd)
    val ip = localLanAddress() ?: return configured
    // Replace the host when it is a wildcard, when it is already current, or
    // when it is the address this phone had LAST time. Comparing against the
    // remembered address is what makes a DHCP change self-heal: the field
    // holds yesterday's IP, which is stale, and is rewritten to today's.
    // A host that is none of those was chosen deliberately and is left alone.
    val shouldReplace = host == "*" || host == "0.0.0.0" ||
        host == ip || (lastKnownIp != null && host == lastKnownIp)
    if (!shouldReplace) return configured
    val tail = trimmed.substring(hostEnd)
    // 5556 is the gateway's SUB port now. An endpoint still carrying it on
    // this phone's own address is the old default rather than a deliberate
    // choice, so migrate it in place: otherwise the device keeps publishing
    // exactly where the gateway is listening, and a direct viewer on 15556
    // never connects.
    val migratedTail =
        if (tail == ":$LEGACY_STREAM_PORT") ":$DEFAULT_STREAM_PORT" else tail
    return trimmed.substring(0, hostStart) + ip + migratedTail
}

private fun extractHost(url: String): String {
    return try {
        URI(url).host ?: url
    } catch (_: Exception) {
        url
    }
}

private fun formatDuration(seconds: Long): String {
    val h = seconds / 3600
    val m = (seconds % 3600) / 60
    val s = seconds % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}

// ═══════════════════════════════════════════════════════════════════════
// Composable: Camera preview area (SurfaceView or permission prompt)
// ═══════════════════════════════════════════════════════════════════════

@androidx.compose.runtime.Composable
private fun CameraPreviewOverlay(
    cameraGranted: Boolean,
    onRequestCamera: () -> Unit,
    previewView: android.view.TextureView,
    displayW: Int,
    displayH: Int
) {
    BoxWithConstraints(
        modifier = Modifier.fillMaxSize().background(Color.Black),
        contentAlignment = Alignment.Center
    ) {
        if (cameraGranted) {
            // Lay the viewfinder out at the stream's exact display aspect and
            // fit it inside the screen: the TextureView then draws the HAL
            // buffer 1:1 (identity transform), so the viewfinder is pixel-for-
            // pixel what the player renders from the same bytes. No zoom, crop
            // or stretch is introduced by the app.
            val dw = (displayW.takeIf { it > 0 } ?: 720).toFloat()
            val dh = (displayH.takeIf { it > 0 } ?: 1280).toFloat()
            val scale = minOf(maxWidth.value / dw, maxHeight.value / dh)
            val w = (dw * scale).roundToInt().coerceAtLeast(1)
            val h = (dh * scale).roundToInt().coerceAtLeast(1)
            androidx.compose.ui.viewinterop.AndroidView(
                factory = { previewView },
                modifier = Modifier
                    .width(w.dp)
                    .height(h.dp)
            )
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color.Black),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Text(
                    text = stringResource(R.string.permission_title),
                    style = MaterialTheme.typography.titleMedium,
                    color = Color.White
                )
                Spacer(Modifier.height(12.dp))
                Text(
                    text = stringResource(R.string.permission_body),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Color.White.copy(alpha = 0.7f)
                )
                Spacer(Modifier.height(20.dp))
                Text(
                    text = stringResource(R.string.app_description),
                    style = MaterialTheme.typography.bodySmall,
                    color = Color.White.copy(alpha = 0.55f),
                    modifier = Modifier.padding(horizontal = 32.dp)
                )
                Spacer(Modifier.height(20.dp))
                androidx.compose.material3.FilledTonalButton(onClick = onRequestCamera) {
                    Text(stringResource(R.string.permission_allow))
                }
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Composable: Stream status dot + text
// ═══════════════════════════════════════════════════════════════════════

// ── Live indicator ────────────────────────────────────────────
// One beat every few seconds, sized in frames so it tracks real traffic.
// 150 frames is five seconds at the 30fps the device publishes; the beat
// FREEZES if the stream stops, which is the point of tying it to frames at
// all. Faster than a few seconds and it stops reading as "alive" and starts
// reading as decoration.
/** Last gateway endpoint the user configured, restored when switching back. */
private const val KEY_LAST_GATEWAY = "last_gateway_endpoint"

private const val PULSE_FRAMES = 60L
private const val PULSE_CYCLE_MS = 2000L

/** Diameter of the Live dot. The ping ring starts the same size and grows to 2x. */
private const val DOT_DP = 9f

/** Brighter than the status dot green so the Live pulse reads instantly. */
private val LIVE_GREEN = Color(0xFF3BE05F)

@androidx.compose.runtime.Composable
private fun StreamStatusRow(
    connectionState: ConnectionState,
    hostName: String,
    streamUptimeSeconds: Long,
    pulseBucket: Long
) {
    // hostName and streamUptimeSeconds are no longer rendered here. The state
    // label sits top-left and the uptime timer top-right, so this composable
    // carries only the state. Kept as parameters so the call site reads the
    // same whichever layout is active.
    val dotColor = when (connectionState) {
        is ConnectionState.Connected -> Color(0xFF4CAF50)   // Green
        is ConnectionState.Connecting,
        is ConnectionState.Reconnecting -> Color(0xFFFFC107) // Amber
        is ConnectionState.Disconnected,
        is ConnectionState.Failed -> Color(0xFFF44336)   // Red
        is ConnectionState.Idle -> Color(0xFF9E9E9E)        // Gray
    }

    val statusText = when (connectionState) {
        is ConnectionState.Connected -> stringResource(R.string.status_live)
        is ConnectionState.Connecting -> stringResource(R.string.status_connecting)
        is ConnectionState.Reconnecting -> stringResource(R.string.status_reconnecting)
        is ConnectionState.Disconnected -> stringResource(R.string.status_stopped)
        is ConnectionState.Failed -> stringResource(R.string.status_failed, connectionState.reason)
        is ConnectionState.Idle -> stringResource(R.string.status_ready)
    }

    // Pulse driven by REAL traffic, not a free-running timer. [pulseBucket]
    // only advances when the publisher actually pushed a message, so the dot
    // freezes if the stream stalls -- which is the whole point of a liveness
    // indicator. A blinking animation that ignores the connection is worse
    // than none: it reports "fine" while nothing is moving.
    val live = connectionState is ConnectionState.Connected
    // The beat, as a single 0 -> 1 ramp. Everything is derived from it, exactly
    // as the CSS `ping` keyframes do: scale 1 -> 2 and opacity 0.75 -> 0.
    val p = remember { Animatable(1f) }
    LaunchedEffect(pulseBucket) {
        if (pulseBucket == 0L) {
            // No frames arriving: the ring is gone, the dot is simply on.
            p.snapTo(1f)
            return@LaunchedEffect
        }
        p.snapTo(0f)
        p.animateTo(
            1f,
            androidx.compose.animation.core.tween(
                durationMillis = PULSE_CYCLE_MS.toInt(),
                easing = androidx.compose.animation.core.LinearOutSlowInEasing
            )
        )
    }
    // Core brightness breathes gently, in step with the same beat.
    val beat = if (live) 1f - p.value * 0.35f else 0.35f
    val liveColor = if (live) LIVE_GREEN else dotColor

    Row(verticalAlignment = Alignment.CenterVertically) {
        // Sized for the ring at its widest (2x the dot) so it is not clipped.
        Box(contentAlignment = Alignment.Center, modifier = Modifier.size(20.dp)) {
            if (live) {
                // The classic single ping: one overlay the SAME size as the
                // dot, scaling to 2x while fading to nothing. Two rings and a
                // slow drift read as decoration; this reads as a heartbeat.
                Box(
                    modifier = Modifier
                        .size(DOT_DP.dp)
                        .scale(1f + p.value)
                        .clip(CircleShape)
                        .background(liveColor.copy(alpha = (1f - p.value) * 0.75f))
                )
            }
            Box(
                modifier = Modifier
                    .size(DOT_DP.dp)
                    .alpha(if (live) 0.75f + beat * 0.25f else beat)
                    .clip(CircleShape)
                    .background(if (live) liveColor else dotColor)
            )
        }
        Spacer(Modifier.width(8.dp))
        Text(
            text = statusText,
            style = MaterialTheme.typography.bodySmall,
            color = Color.White,
            fontWeight = FontWeight.SemiBold
        )
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Composable: Settings sheet (kept alive, hidden off-screen)
// ═══════════════════════════════════════════════════════════════════════

@OptIn(ExperimentalLayoutApi::class)
@androidx.compose.runtime.Composable
private fun SettingsSheetOverlay(
    showSettings: Boolean,
    streamUrl: String,
    onEndpointChange: (streamUrl: String) -> Unit,
    streamMode: StreamMode,
    onStreamModeChange: (StreamMode) -> Unit,
    directPublish: Boolean,
    onDirectPublishChange: (Boolean) -> Unit,
    focusMode: FocusMode,
    onFocusModeChange: (FocusMode) -> Unit,
    meshPub: String,
    meshPriv: String,
    meshPrivReveal: Boolean,
    onMeshPubChange: (String) -> Unit,
    onMeshPrivChange: (String) -> Unit,
    onMeshPrivRevealToggle: () -> Unit,
    isMetricsRunning: Boolean,
    notificationGranted: Boolean,
    locationGranted: Boolean,
    batteryOptGranted: Boolean,
    onRequestNotification: () -> Unit,
    onRequestLocation: () -> Unit,
    onRequestBattery: () -> Unit,
    onDismiss: () -> Unit
) {
    val focusManager = LocalFocusManager.current
    val sheetProgress by animateFloatAsState(
        targetValue = if (showSettings) 1f else 0f,
        animationSpec = tween(durationMillis = 300, easing = FastOutSlowInEasing),
        label = "settingsSheetProgress"
    )

    LaunchedEffect(showSettings) {
        if (!showSettings) focusManager.clearFocus()
    }

    BackHandler(enabled = showSettings) { onDismiss() }

    Box(Modifier.fillMaxSize()) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 0.32f * sheetProgress))
                .then(
                    if (showSettings) {
                        Modifier.pointerInput(Unit) { detectTapGestures { onDismiss() } }
                    } else {
                        Modifier
                    }
                )
        )

        Surface(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .widthIn(max = 640.dp)
                .fillMaxWidth()
                .graphicsLayer {
                    translationY = (1f - sheetProgress) * size.height
                    alpha = sheetProgress.coerceIn(0f, 1f)
                },
            shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            tonalElevation = 8.dp
        ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                // The sheet is edge-to-edge and its surface reaches the status
                // bar, so it must inset for it or the "Settings" title and the
                // status icons overlap.
                .statusBarsPadding()
                .windowInsetsPadding(WindowInsets.navigationBars)
                .padding(horizontal = 24.dp)
                .padding(top = 20.dp)
                .padding(bottom = 32.dp)
        ) {
            // ── Header: title + save ────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = stringResource(R.string.settings_title),
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f)
                )
                // No save button. Everything below applies the moment it
                // changes, the way the toggles already did, and persisting it
                // on each keystroke costs nothing next to the confusion of a
                // separate save step that people forget to press.
            }

            Spacer(Modifier.height(8.dp))

            // ── Section: Stream ──────────────────────────────────
            // ── Section: Publish to ───────────────────────────────────
            // Stream Settings and Publish to are one decision, not two: the
            // endpoint only means anything once you know who is listening. So
            // they are a single section, and in DIRECT mode the endpoint is
            // not shown at all -- it is this phone's own address, which the
            // app detects and keeps current, so there is nothing to type and
            // nothing useful to show.
            // Same pattern as Stream Mode: the `i` explains whichever target is
            // actually selected, rather than a caption describing both at once.
            SectionHeader(stringResource(R.string.publish_header)) {
                InfoTooltip(
                    if (directPublish) stringResource(R.string.publish_direct_hint)
                    else stringResource(R.string.publish_server_hint)
                )
            }

            Spacer(Modifier.height(10.dp))

            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                listOf(false to R.string.publish_server, true to R.string.publish_direct)
                    .forEach { (direct, label) ->
                        FilterChip(
                            selected = directPublish == direct,
                            onClick = { onDirectPublishChange(direct) },
                            label = { Text(stringResource(label)) },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = MaterialTheme.colorScheme.primary,
                                selectedLabelColor = MaterialTheme.colorScheme.onPrimary
                            )
                        )
                    }
            }

            Spacer(Modifier.height(8.dp))

            // The caption that used to sit here said the same sentence as the
            // tooltip, verbatim. One of them is enough; the tooltip is the one
            // that can also explain the option you did not pick.

            if (!directPublish) {
                Spacer(Modifier.height(12.dp))

                OutlinedTextField(
                    value = streamUrl,
                    onValueChange = onEndpointChange,
                    label = { Text(stringResource(R.string.publish_endpoint_label)) },
                    placeholder = { Text(defaultGatewayEndpoint()) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth()
                )
            }

            SectionDivider()

            // ── Section: Stream mode ──────────────────────────────
            // Explains whichever mode is currently picked, so the tooltip is never out
            // of step with the chip the user just tapped. The old caption
            // ("A/V to ffplay. Traffic: video + metrics.") described both modes
            // at once in two fragments and told you nothing about which one you
            // were actually in.
            SectionHeader(stringResource(R.string.mode_header)) {
                InfoTooltip(
                    when (streamMode) {
                        StreamMode.STREAMING -> stringResource(R.string.mode_streaming_hint)
                        StreamMode.TRAFFIC -> stringResource(R.string.mode_traffic_hint)
                    }
                )
            }

            Spacer(Modifier.height(10.dp))

            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                StreamMode.entries.forEach { mode ->
                    FilterChip(
                        selected = mode == streamMode,
                        onClick = { onStreamModeChange(mode) },
                        label = { Text(stringResource(mode.label)) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = MaterialTheme.colorScheme.primary,
                            selectedLabelColor = MaterialTheme.colorScheme.onPrimary
                        )
                    )
                }
            }

            SectionDivider()

            // ── Section: Focus ─────────────────────────────────────
            SectionHeader(stringResource(R.string.focus_header))

            Spacer(Modifier.height(10.dp))

            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                FocusMode.entries.forEach { mode ->
                    FilterChip(
                        selected = mode == focusMode,
                        onClick = { onFocusModeChange(mode) },
                        label = { Text(stringResource(mode.label)) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = MaterialTheme.colorScheme.primary,
                            selectedLabelColor = MaterialTheme.colorScheme.onPrimary
                        )
                    )
                }
            }

            SectionDivider()

            // ── Section: Mesh discovery keys ─────────────────────────
            // The discovery channel between devices (ZRE over Curve) runs
            // from a key pair the operator generates OFF this phone — the
            // dashboard shows both keys as text, and pasting them here is
            // the whole flow. No generation on-device: the pair is created
            // in the avedge/Dashboard tooling, which can audit and rotate.
            SectionHeader(stringResource(R.string.mesh_header)) {
                InfoTooltip(stringResource(R.string.mesh_hint))
            }

            Spacer(Modifier.height(10.dp))

            OutlinedTextField(
                value = meshPub,
                onValueChange = onMeshPubChange,
                label = { Text(stringResource(R.string.mesh_pub_label)) },
                singleLine = true,
                supportingText = {
                    Text(stringResource(R.string.mesh_char_count, meshPub.length))
                },
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(8.dp))

            OutlinedTextField(
                value = meshPriv,
                onValueChange = onMeshPrivChange,
                label = { Text(stringResource(R.string.mesh_priv_label)) },
                singleLine = true,
                visualTransformation = if (meshPrivReveal) VisualTransformation.None
                    else PasswordVisualTransformation(),
                trailingIcon = {
                    TextButton(onClick = onMeshPrivRevealToggle) {
                        Text(stringResource(
                            if (meshPrivReveal) R.string.mesh_hide
                            else R.string.mesh_show
                        ))
                    }
                },
                supportingText = {
                    Text(stringResource(R.string.mesh_char_count, meshPriv.length))
                },
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(8.dp))

            Text(
                text = if (meshPub.length == 40 && meshPriv.length == 40) {
                    stringResource(R.string.mesh_status_ready)
                } else {
                    stringResource(R.string.mesh_status_wait)
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            // Metrics and Metrics Service only exist in TRAFFIC mode.
            //
            // In STREAMING the phone muxes H.264+AAC into MPEG-TS and sends
            // it as SINGLE-FRAME messages, so every client can recv() it
            // directly. Telemetry is 2-part multipart, and FFmpeg's zmq
            // demuxer takes frame 0 and drops the rest -- which would feed
            // the seven bytes of "metrics" into the TS stream and corrupt it.
            // There is also nowhere to put it: the gateway re-publishes to all
            // topics.
            //
            // Showing a permanently "Stopped" service was worse than hiding it.
            // The control could never be used in this mode, and the caption
            // ("press play to start") pointed at a button that would not start it.
            if (streamMode == StreamMode.TRAFFIC) {
                SectionDivider()

                // ── Section: Metrics ──────────────────────────────────
                SectionHeader(stringResource(R.string.metrics_header))

                Spacer(Modifier.height(10.dp))

                Text(
                    text = stringResource(R.string.metrics_body),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                SectionDivider()

                // ── Section: Metrics Service Status ───────────────────────
                SectionHeader(stringResource(R.string.metrics_service_header))

                Spacer(Modifier.height(12.dp))

                val allGranted = notificationGranted && locationGranted && batteryOptGranted

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(12.dp)
                            .clip(CircleShape)
                            .background(
                                if (isMetricsRunning) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.error
                            )
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = when {
                            isMetricsRunning -> stringResource(R.string.metrics_running)
                            !allGranted -> stringResource(R.string.metrics_missing_permissions)
                            else -> stringResource(R.string.metrics_press_play)
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }

                if (!allGranted) {
                    Spacer(Modifier.height(12.dp))
                    if (!notificationGranted) {
                        PermissionItem(
                            label = stringResource(R.string.perm_notifications),
                            granted = false,
                            onClick = onRequestNotification
                        )
                        Spacer(Modifier.height(4.dp))
                    }
                    if (!locationGranted) {
                        PermissionItem(
                            label = stringResource(R.string.perm_location),
                            granted = false,
                            onClick = onRequestLocation
                        )
                        Spacer(Modifier.height(4.dp))
                    }
                    if (!batteryOptGranted) {
                        PermissionItem(
                            label = stringResource(R.string.perm_battery),
                            granted = false,
                            onClick = onRequestBattery
                        )
                    }
                }
            }

            SectionDivider()

            // Language sits at the bottom on purpose. It changes how every
            // other label above is spelled, which makes it tempting to lead
            // with it, but it is not something anyone comes here to change --
            // streaming settings are. Parking it under the telemetry controls
            // keeps the things people actually adjust in the first screenful.
            SectionHeader(stringResource(R.string.language_header))

            Spacer(Modifier.height(10.dp))

            com.visioncamhealth.ui.LanguageSelector()
        }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Composable: Section separator
// ═══════════════════════════════════════════════════════════════════════

/**
 * Separates one settings section from the next.
 *
 * Every section boundary uses this rather than a bare Spacer, so the rhythm
 * is identical throughout. Before it existed, Metrics had a divider and
 * Stream Mode → Focus had nothing at all, so the Focus header sat directly
 * against the chips above it and read as part of that section.
 */
@androidx.compose.runtime.Composable
private fun SectionDivider() {
    Spacer(Modifier.height(24.dp))
    HorizontalDivider(
        color = MaterialTheme.colorScheme.outlineVariant,
        thickness = 0.5.dp
    )
    Spacer(Modifier.height(20.dp))
}

// ═══════════════════════════════════════════════════════════════════════
// Composable: Section header
// ═══════════════════════════════════════════════════════════════════════

@androidx.compose.runtime.Composable
private fun SectionHeader(
    title: String,
    trailing: @androidx.compose.runtime.Composable (() -> Unit)? = null
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .width(4.dp)
                .height(20.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(MaterialTheme.colorScheme.primary)
        )
        Spacer(Modifier.width(12.dp))
        Text(
            text = title,
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface,
            fontWeight = FontWeight.SemiBold
        )
        // Keeps help next to the thing it explains rather than as loose
        // caption text under it, so the section says one thing on screen.
        if (trailing != null) {
            Spacer(Modifier.width(6.dp))
            trailing()
        }
    }
}

/**
 * An `i` that explains the section it sits in.
 *
 * Tapping shows the tooltip, not just long-press: these sheets are operated
 * one-handed on a phone, where long-press is not a gesture anyone reaches for
 * without being told. isPersistent is what makes show() stick until dismissed.
 */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@androidx.compose.runtime.Composable
private fun InfoTooltip(text: String) {
    val tooltipState = androidx.compose.material3.rememberTooltipState(isPersistent = true)
    val scope = androidx.compose.runtime.rememberCoroutineScope()

    androidx.compose.material3.TooltipBox(
        positionProvider = androidx.compose.material3.TooltipDefaults
            .rememberTooltipPositionProvider(
                androidx.compose.material3.TooltipAnchorPosition.Above
            ),
        tooltip = {
            PlainTooltip { Text(text) }
        },
        state = tooltipState
    ) {
        IconButton(
            onClick = { scope.launch { tooltipState.show() } },
            modifier = Modifier.size(28.dp)
        ) {
            Icon(
                Icons.Default.Info,
                contentDescription = stringResource(R.string.info_more),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(18.dp)
            )
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Composable: Permission item (preserved from original)
// ═══════════════════════════════════════════════════════════════════════

@androidx.compose.runtime.Composable
private fun PermissionItem(
    label: String,
    granted: Boolean,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier
                .size(12.dp)
                .clip(CircleShape)
                .background(
                    if (granted) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.error
                )
        )
        Spacer(Modifier.width(12.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f)
        )
        Text(
            text = stringResource(if (granted) R.string.perm_granted else R.string.perm_tap_to_allow),
            style = MaterialTheme.typography.bodySmall,
            color = if (granted) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.error,
            fontWeight = FontWeight.Medium
        )
    }
}

@androidx.compose.ui.tooling.preview.Preview(showBackground = false)
@androidx.compose.runtime.Composable
private fun PermissionItemPreview() {
    MaterialTheme {
        Column(modifier = Modifier.padding(16.dp)) {
            PermissionItem(
                label = stringResource(R.string.perm_camera_title),
                granted = true,
                onClick = {}
            )
            Spacer(Modifier.height(8.dp))
            PermissionItem(
                label = stringResource(R.string.perm_location_title),
                granted = false,
                onClick = {}
            )
        }
    }
}
