import QtQuick
import Quickshell.Io

// A service-only plugin: no replacement wallpaper renderer or bar widget.
// Process lifetime is tied to the plugin, including disable and hot-reload.
Item {
    id: root
    property var shell: null
    property var manifest: null
    visible: false

    Timer {
        id: retry
        interval: 1000
        onTriggered: watcher.running = true
    }

    Process {
        id: watcher
        command: ["python3", decodeURIComponent(String(Qt.resolvedUrl("bin/omarchy-bg-intros")).replace(/^file:\/\//, "")), "watch"]
        running: true
        onExited: retry.start()
    }

    Component.onDestruction: {
        retry.stop()
        watcher.running = false
    }
}
