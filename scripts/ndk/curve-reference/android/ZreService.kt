package com.visioncamhealth.zyre

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import com.visioncamhealth.R
import java.util.concurrent.Executors

/**
 * The zre node as an always-on foreground service. Runs from boot
 * (BootReceiver) and alongside the normal app work; shares nothing
 * with the stream/telemetry sockets — separate transport, thread and
 * notification.
 *
 * FGS type: `connectedDevice` (a network connection to other devices —
 * exact use case). Deliberately NOT `dataSync`: on Android 15+
 * platforms, apps targeting SDK 35+ may not start a dataSync FGS from
 * BOOT_COMPLETED (ForegroundServiceStartNotAllowedException) and any
 * dataSync FGS is killed after 6 h/24 h (onTimeout). connectedDevice
 * is on neither list and has no runtime timeout.
 *
 * Curve is ALWAYS on: the node is created from the Curve pair the
 * operator sets in the app settings (generated off-device — dashboard
 * or scripts/ndk tooling — and pasted as text; nothing is generated on
 * the device and nothing ships in the APK). A missing or bad pair
 * refuses to run plain (see ZreNative.create), same contract as the
 * verified zc_peer harness.
 */
class ZreService : Service() {

    // ONE execution thread for every start/stop/rekey decision. The
    // ghost-BOOT_COMPLETED quirk (fires on every app launch) plus the
    // launcher's own start can race startZre within milliseconds — two
    // coroutines read a pump handle the other has not set yet and the
    // recreate path got rc=-2 (observed on the emulator 06:23). The
    // native layer's own discipline (ONE pump thread, ONE lock) already
    // assumes serialized control decisions; the executor is the Kotlin
    // half of that contract.
    private val exec = Executors.newSingleThreadExecutor { r ->
        Thread(r, "zre-exec")
    }

    @Volatile private var pumping = false
    @Volatile private var stopping = false
    @Volatile private var greeted = false
    private var pumpThread: HandlerThread? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action ?: ACTION_START // null intent = sticky restart
        exec.execute {
            try {
                when (action) {
                    ACTION_START -> {
                        Log.i(TAG, "zre service start (bootElapsed=${bootAgeMs()}ms)")
                        // Promote to foreground FIRST: after a
                        // startForegroundService() the promotion must
                        // happen within the start-foreground window
                        // (docs: "a few seconds"; AOSP default 30 s) or
                        // the process is killed with
                        // ForegroundServiceDidNotStartInTimeException.
                        try {
                            if (Build.VERSION.SDK_INT >= 29) {
                                startForeground(
                                    NOTIFICATION_ID, buildNotification(),
                                    ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
                            } else {
                                @Suppress("DEPRECATION")
                                startForeground(NOTIFICATION_ID, buildNotification())
                            }
                        } catch (e: SecurityException) {
                            Log.e(TAG, "foreground promotion refused (notifications/type)", e)
                            stopSelf()
                            return@execute
                        } catch (e: IllegalStateException) {
                            // ForegroundServiceStartNotAllowedException
                            // derives from IllegalStateException; e.g.
                            // force-stopped app state rejects the
                            // boot-promotion path.
                            Log.e(TAG, "foreground promotion not allowed", e)
                            stopSelf()
                            return@execute
                        }
                        startZre(intent)
                    }
                    ACTION_STOP -> requestStop("stop action")
                    ACTION_REKEY -> {
                        // Compromise response, as a settings flow: the
                        // operator pastes a replacement pair into settings
                        // first (this action fires after the save), and this
                        // rebuilds the node so the running identity is the
                        // pasted one. Prefs are not wiped here — the stored
                        // pair IS the identity.
                        Log.i(TAG, "rekey requested: rebuilding with stored identity")
                        stopZreNode()           // rebuild with the new identity
                        startZre(intent)
                    }
                }
            } catch (t: Throwable) {
                Log.e(TAG, "zre action $action failed", t)
            }
        }
        // START_STICKY keeps "always there" honest: if the system kills
        // us, it re-delivers with a null intent and the node comes back.
        return START_STICKY
    }

    private fun startZre(intent: Intent?) {
        val gossip = intent?.getStringExtra(EXTRA_GOSSIP)
        val iface = intent?.getStringExtra(EXTRA_IFACE)
        val name = (android.os.Build.MODEL ?: "camhealth")
            .replace(Regex("[^A-Za-z0-9._-]"), "-")
            .take(24)
        try {
            // Identity: the operator owns the Curve pair. It is
            // generated OFF the device (dashboard or the scripts/ndk
            // tooling) and pasted into the app settings; this service
            // only reads what is stored. Nothing is generated here —
            // never add key generation inside the app.
            val prefs = getSharedPreferences(PREFS_ID, Context.MODE_PRIVATE)
            val pub = prefs.getString(PREF_KEY_PUB, null)?.trim()
            val sec = prefs.getString(PREF_KEY_SEC, null)?.trim()
            if (pub.isNullOrBlank() || sec.isNullOrBlank()
                || pub.length != 40 || sec.length != 40) {
                Log.i(TAG, "no curve keys configured; refusing to run plain" +
                        " (paste the key pair in settings)")
                stopZreNode()
                stopSelf()
                return
            }
            var handle = ZreNative.create(name, pub, sec, iface)
            if (handle == -1L) {
                Log.e(TAG, "bad curve keys; refusing to run plain")
                stopSelf()
                return
            }
            if (handle == -2L) {
                // A node already runs in this process (e.g. the
                // boot-start leg). When this request asks for
                // gossip, rebuild the node so the rendezvous
                // applies (zyre does not support connecting a
                // gossip endpoint after start).
                if (gossip == null) { Log.i(TAG, "zre node already up; reattach"); return }
                Log.i(TAG, "zre node up in beacon mode; recreating with gossip")
                // stopZreNode() nulls the handle; the recreate must
                // wait on the OLD pump thread's exit or create races
                // the in-flight destroy and returns -2 (seen on the
                // emulator: create arrived 1 ms before the destroy).
                val oldPump = pumpThread
                stopZreNode()
                oldPump?.join(5000)
                stopping = false
                handle = ZreNative.create(name, pub, sec, iface)
                if (handle <= 0L) { Log.e(TAG, "recreate refused rc=$handle"); stopSelf(); return }
            } else if (handle <= 0L) {
                Log.e(TAG, "create failed rc=$handle")
                stopSelf()
                return
            }
            if (stopping) { ZreNative.stop(); return }
            greeted = false                 // fresh greeting per node
            if (gossip != null) {
                val rc = ZreNative.gossipConnect(gossip)
                Log.i(TAG, "gossip connect rc=$rc endpoint=$gossip")
            }
            if (ZreNative.start() != 0) { Log.e(TAG, "zyre_start failed"); stopZreNode(); stopSelf(); return }
            ZreNative.join(GROUP)
            // An announce shout fires only AFTER a peer has entered
            // (a start-time shout reaches nobody: the group has no
            // members yet). The deliverable is picked up in pumpLoop.
            Log.i(TAG, "zre node up uuid=${ZreNative.uuid()} name=$name group=$GROUP gossip=${gossip != null}")
            if (pumping) return   // a pump from a prior START is alive
            pumping = true
            pumpThread = HandlerThread("zre-pump").apply { start() }
            Handler(pumpThread!!.looper).post { pumpLoop() }
        } catch (e: Exception) {
            Log.e(TAG, "zre startup failed", e)
            stopZreNode()
        }
    }

    /** Runs on the dedicated pump thread; nextEvent() blocks in native. */
    private fun pumpLoop() {
        try {
            while (pumping) {
                val json = ZreNative.nextEvent()
                if (json.isEmpty() || json == "{}") break   // node stopping
                Log.i(TAG, "zre event: $json")
                // First time a peer enters, announce ourselves: peers
                // that discover us late get the one-shot greeting too.
                if (!greeted && json.contains("\"type\":\"ENTER\"")) {
                    greeted = true
                    ZreNative.shout(GROUP, "zre-ready")
                    Log.i(TAG, "greeted the mesh (zre-ready)")
                }
            }
        } finally {
            // Single-thread teardown: the pump thread owns the destroy
            // (departure beacon first, then the ZAP actor) — nobody
            // races it, matching the verified zc_peer discipline.
            ZreNative.stop()
            Log.i(TAG, "zre node stopped")
        }
    }

    private fun requestStop(reason: String) {
        Log.i(TAG, "zre service stop ($reason)")
        stopZreNode()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /** Interrupts the pump; the pump thread then runs ZreNative.stop(). */
    private fun stopZreNode() {
        pumping = false
        stopping = true
        ZreNative.signalStop()
        pumpThread?.quitSafely()
        pumpThread = null
    }

    override fun onDestroy() {
        stopZreNode()
        exec.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /** Only time-limited types (dataSync etc.) get this; connectedDevice
     *  does not. Defensive: same handling as MetricsService. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        Log.w(TAG, "fgs timeout (type=$fgsType) — stopping")
        requestStop("onTimeout")
    }

    private fun bootAgeMs(): String {
        val age = SystemClock.elapsedRealtime()
        val min = age / 60000
        val sec = (age % 60000) / 1000
        return "$min:$sec"
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < 26) return   // no channels before API 26
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.zre_notification_channel_name),
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = getString(R.string.zre_notification_channel_description)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(getString(R.string.zre_notification_body))
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setOngoing(true)
            .build()

    companion object {
        const val ACTION_START = "com.visioncamhealth.action.ZRE_START"
        const val ACTION_STOP = "com.visioncamhealth.action.ZRE_STOP"
        const val ACTION_REKEY = "com.visioncamhealth.action.ZRE_REKEY"
        const val EXTRA_GOSSIP = "zre_gossip"    // udp-free rendezvous endpoint
        const val EXTRA_IFACE = "zre_iface"      // pinned interface (optional)
        private const val GROUP = "CHAT"         // matches the phone trials
        private const val CHANNEL_ID = "zre_service"
        private const val NOTIFICATION_ID = 2    // MetricsService uses 1
        private const val TAG = "visioncamhealth-Zre"
        // Read by MainActivity's settings sheet too: the identity lives in
        // these prefs and the operator manages it from the dashboard flow
        // (paste the pair), so both surfaces must use the exact same keys.
        const val PREFS_ID = "zre_identity"
        const val PREF_KEY_PUB = "curve_pub"
        const val PREF_KEY_SEC = "curve_sec"
    }
}
