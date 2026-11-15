"""3D skin preview — a blocky figure wearing the skin.

Qt/OpenGL only (the GUI's side of the boundary): the scene data
comes from skins.figure_boxes, which is Qt-free, and the texture
is the canonical 64x64 skin from skins.reassemble_skin, so a
classic 64x32 skin previews with its mirrored left limbs. One
texture, one vertex buffer, one draw call — a dozen boxes at
most, nothing like a game engine.

The widget builds its geometry in __init__ (pure data, no GL
context needed), and creates the texture and buffers in
initializeGL, when the context exists. Drag to orbit: the yaw
and pitch are plain attributes, so tests can set them without
a mouse. The sampler uniform is never set — it defaults to
texture unit 0, which is where the texture is bound.

Cleanup: the GL objects are destroyed in hideEvent, while the
context is still current — destroying the texture after the
context is gone makes Qt log a warning on close. paintGL
re-creates them if they are gone, so hiding and showing again
(a minimize, say) keeps working. The context's own
aboutToBeDestroyed signal was tried first and does not work
here: the widget's connections are already gone by the time
the context is destroyed, so the slot never runs.

PySide6 exposes no GL enum constants and there is no PyOpenGL
here, so the five enums used are module constants (stable
since GL 1.0). The shader is GLSL 110 (attribute/varying,
texture2D), which the default compatibility-profile context
compiles.
"""

import struct

from PySide6.QtCore import Qt
from PySide6.QtGui import QMatrix4x4
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader,
                              QOpenGLShaderProgram, QOpenGLTexture,
                              QOpenGLVertexArrayObject)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from .qtimage import to_qimage
from .skins import figure_boxes, reassemble_skin

# OpenGL enums (see the module docstring).
_GL_TRIANGLES = 0x0004
_GL_COLOR_BUFFER_BIT = 0x00004000
_GL_DEPTH_BUFFER_BIT = 0x00000100
_GL_DEPTH_TEST = 0x0B71
_GL_CULL_FACE = 0x0B44
_GL_FLOAT = 0x1406

# Per-face brightness: flat shading by face, so the figure
# reads as solid without a light.
FACE_BRIGHTNESS = {"top": 1.0, "front": 0.85, "right": 0.78,
                   "left": 0.66, "back": 0.72, "bottom": 0.55}

_VERTEX_SHADER = """
attribute vec3 position;
attribute vec2 texCoord;
attribute float brightness;
varying vec2 vTexCoord;
varying float vBrightness;
uniform mat4 mvp;
void main() {
    gl_Position = mvp * vec4(position, 1.0);
    vTexCoord = texCoord;
    vBrightness = brightness;
}
"""

_FRAGMENT_SHADER = """
varying vec2 vTexCoord;
varying float vBrightness;
uniform sampler2D tex;
void main() {
    vec4 color = texture2D(tex, vTexCoord);
    if (color.a < 0.5) discard;
    gl_FragColor = vec4(color.rgb * vBrightness, 1.0);
}
"""


def _face_vertices(box, face):
    """The 6 vertices (2 triangles) of one box face: each an
    (x, y, z, u, v, brightness) tuple. The face rect measures v
    from the top of the image, and QOpenGLTexture uploads the
    QImage that way round too (v=0 is the image's top row —
    verified with a coordinate-encoding texture: a vertex at
    GL v 0.875 samples image row 56), so the rect's v is used
    directly, NOT flipped. The back face runs u the other way
    (it is drawn as seen from behind), the top face's front
    edge is the rect's bottom edge, and the bottom face's front
    edge is the rect's top edge."""
    (cx, cy, cz), (w, h, d) = box["center"], box["size"]
    x0, x1 = cx - w / 2.0, cx + w / 2.0
    y0, y1 = cy - h / 2.0, cy + h / 2.0
    z0, z1 = cz - d / 2.0, cz + d / 2.0
    u0, v0, u1, v1 = box["faces"][face]
    vt, vb = v0, v1  # top edge, bottom edge in GL v
    b = FACE_BRIGHTNESS[face]
    # Each face: 4 corners (position, u, v); two triangles
    # (0, 1, 2) and (0, 2, 3). Culling stays off, so the
    # winding only needs to be consistent.
    if face == "front":  # +z
        corners = [(x0, y0, z1, u0, vb), (x1, y0, z1, u1, vb),
                   (x1, y1, z1, u1, vt), (x0, y1, z1, u0, vt)]
    elif face == "back":  # -z, u runs the other way
        corners = [(x1, y0, z0, u0, vb), (x0, y0, z0, u1, vb),
                   (x0, y1, z0, u1, vt), (x1, y1, z0, u0, vt)]
    elif face == "right":  # -x, the character's right side
        corners = [(x0, y0, z0, u0, vb), (x0, y0, z1, u1, vb),
                   (x0, y1, z1, u1, vt), (x0, y1, z0, u0, vt)]
    elif face == "left":  # +x
        corners = [(x1, y0, z1, u0, vb), (x1, y0, z0, u1, vb),
                   (x1, y1, z0, u1, vt), (x1, y1, z1, u0, vt)]
    elif face == "top":  # +y, the front edge is the rect's bottom
        corners = [(x0, y1, z1, u0, vb), (x1, y1, z1, u1, vb),
                   (x1, y1, z0, u1, vt), (x0, y1, z0, u0, vt)]
    else:  # "bottom", -y, the front edge is the rect's top
        corners = [(x0, y0, z1, u0, vt), (x1, y0, z1, u1, vt),
                   (x1, y0, z0, u1, vb), (x0, y0, z0, u0, vb)]
    out = []
    for i in (0, 1, 2, 0, 2, 3):
        x, y, z, u, v = corners[i]
        out.append((x, y, z, u, v, b))
    return out


def build_figure_vertices(boxes):
    """All boxes as one flat float list, 6 floats per vertex
    (x, y, z, u, v, brightness). Pure data — no GL context
    needed, so tests can check the geometry headless."""
    vertices = []
    for box in boxes:
        for face in ("top", "bottom", "right", "front", "left",
                     "back"):
            for vertex in _face_vertices(box, face):
                vertices.extend(vertex)
    return vertices


class SkinPreviewWidget(QOpenGLWidget):
    """A blocky figure wearing a skin, drawn with one texture
    and one draw call. Takes a skin of either layout — the
    texture is the reassembled 64x64 skin. Drag to orbit."""

    # Default view: a three-quarter look at the front, slightly
    # from above.
    DEFAULT_YAW = -30.0
    DEFAULT_PITCH = 12.0

    def __init__(self, image, parent=None):
        super().__init__(parent)
        skin = reassemble_skin(image)
        self.boxes = figure_boxes(skin)
        self.vertices = build_figure_vertices(self.boxes)
        self._qimage = to_qimage(skin)
        self.yaw = self.DEFAULT_YAW
        self.pitch = self.DEFAULT_PITCH
        self._drag_pos = None
        self._program = None
        self._texture = None
        self._vao = None
        self._vbo = None
        self._mvp_loc = -1
        self._aspect = 1.0
        self.setMinimumSize(320, 360)

    def initializeGL(self):
        self._setup_gl()

    def _setup_gl(self):
        """Create the shader program, texture, and buffers.
        Called from initializeGL, and from paintGL when the
        objects were cleaned up by a hide (see hideEvent)."""
        gl = self.context().functions()
        program = QOpenGLShaderProgram(self)
        program.bindAttributeLocation("position", 0)
        program.bindAttributeLocation("texCoord", 1)
        program.bindAttributeLocation("brightness", 2)
        program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Vertex, _VERTEX_SHADER)
        program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment, _FRAGMENT_SHADER)
        if not program.link():
            raise RuntimeError(
                "skin preview shader failed to link: %s"
                % program.log())
        self._program = program
        self._mvp_loc = program.uniformLocation("mvp")
        texture = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        texture.setData(self._qimage)
        texture.setMinMagFilters(QOpenGLTexture.Filter.Nearest,
                                 QOpenGLTexture.Filter.Nearest)
        texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        self._texture = texture
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        vbo.create()
        vbo.bind()
        data = struct.pack("<%df" % len(self.vertices),
                           *self.vertices)
        vbo.allocate(data, len(data))
        program.bind()
        program.setAttributeBuffer(0, _GL_FLOAT, 0, 3, 24)
        program.enableAttributeArray(0)
        program.setAttributeBuffer(1, _GL_FLOAT, 12, 2, 24)
        program.enableAttributeArray(1)
        program.setAttributeBuffer(2, _GL_FLOAT, 20, 1, 24)
        program.enableAttributeArray(2)
        program.release()
        vbo.release()
        vao.release()
        self._vao = vao
        self._vbo = vbo
        gl.glEnable(_GL_DEPTH_TEST)
        gl.glDisable(_GL_CULL_FACE)

    def _cleanup_gl(self):
        """Destroy the GL objects while the context is still
        current (see hideEvent). The shader program has no
        explicit destroy and is freed with the widget."""
        if self._texture is not None:
            self._texture.destroy()
            self._texture = None
        if self._vbo is not None:
            self._vbo.destroy()
            self._vbo = None
        if self._vao is not None:
            self._vao.destroy()
            self._vao = None
        self._program = None

    def hideEvent(self, event):
        # The dialog is closing (or minimizing): destroy the GL
        # objects now, while the context is still current, so
        # the texture is not left to be destroyed after the
        # context is gone. paintGL re-creates them if the
        # widget is shown again.
        if self._program is not None:
            self.makeCurrent()
            self._cleanup_gl()
        super().hideEvent(event)

    def resizeGL(self, width, height):
        self.context().functions().glViewport(0, 0, width, height)
        self._aspect = width / max(1, height)

    def paintGL(self):
        gl = self.context().functions()
        gl.glClearColor(0.14, 0.14, 0.16, 1.0)
        gl.glClear(_GL_COLOR_BUFFER_BIT | _GL_DEPTH_BUFFER_BIT)
        if self._program is None:
            self._setup_gl()
        self._program.bind()
        self._program.setUniformValue(self._mvp_loc, self._mvp())
        self._texture.bind(0)
        self._vao.bind()
        gl.glDrawArrays(_GL_TRIANGLES, 0, len(self.vertices) // 6)
        self._vao.release()
        self._texture.release()
        self._program.release()

    def _mvp(self):
        """Ortho projection fitted to the 32-tall figure, times
        the orbit rotation about the figure's center."""
        half_h = 20.0
        half_w = half_h * self._aspect
        proj = QMatrix4x4()
        proj.ortho(-half_w, half_w, -half_h, half_h, -100.0, 100.0)
        view = QMatrix4x4()
        view.rotate(self.yaw, 0.0, 1.0, 0.0)
        view.rotate(self.pitch, 1.0, 0.0, 0.0)
        view.translate(0.0, -16.0, 0.0)
        return proj * view

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.position()

    def mouseMoveEvent(self, event):
        if self._drag_pos is None:
            return
        pos = event.position()
        self.yaw += (pos.x() - self._drag_pos.x()) * 0.5
        self.pitch = max(-80.0, min(
            80.0, self.pitch + (pos.y() - self._drag_pos.y()) * 0.5))
        self._drag_pos = pos
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


class SkinPreviewDialog(QDialog):
    """The 3D skin preview in its own window (U1): the figure
    plus one plain-words line about the drag control. Takes a
    skin of either layout."""

    def __init__(self, parent, image):
        super().__init__(parent)
        self.setWindowTitle("3D skin preview")
        # Delete on close, unlike the plain dialogs: the preview
        # holds a GL context and texture, and deleting the
        # dialog destroys them (via _cleanup_gl) instead of
        # leaving a context behind per opening.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(420, 540)
        layout = QVBoxLayout(self)
        self.preview = SkinPreviewWidget(image)
        self.preview.setToolTip("Drag to turn the figure around")
        layout.addWidget(self.preview, 1)
        hint = QLabel("Drag the figure to turn it around and see "
                      "every side.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setToolTip("Close the preview")
        self.close_btn.clicked.connect(self.accept)
        button_row.addWidget(self.close_btn)
        layout.addLayout(button_row)
