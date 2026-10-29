package com.visioncamhealth.zyre

/**
 * JNI surface to the statically-linked libzyre node. CURVE is ALWAYS
 * on: create() refuses a bad key pair (returns -1) rather than ever
 * falling back to plain — same contract as the verified zc_peer
 * harness (scripts/ndk/README.md trials).
 *
 * Threading: nextEvent() and stop() belong to the ONE pump thread
 * (they block in native); everything else is safe from any thread.
 * Use signalStop() from another thread to unblock the pump.
 */
object ZreNative {
    init { System.loadLibrary("zrejni") }

    /** Returns the node handle, -1 on bad Curve keys, -2 if already built. */
    external fun create(name: String, pubZ85: String, secZ85: String,
                        iface: String?): Long

    /** Optional UDP-free rendezvous (emulator NAT, AP isolation). Call before start(). */
    external fun gossipConnect(endpoint: String): Int

    external fun start(): Int
    external fun join(group: String)
    external fun shout(group: String, text: String)
    external fun whisper(peer: String, text: String)

    /** Blocking; returns one event as JSON, or "{}" once the node stops. */
    external fun nextEvent(): String

    /** Cross-thread teardown trigger: unblocks a nextEvent() in flight. */
    external fun signalStop()

    /** Full teardown; pump-thread-only (departure beacon, then destroy). */
    external fun stop()

    external fun uuid(): String
}
