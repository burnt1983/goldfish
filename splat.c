/* Fast sphere and capsule splat. Fur shell is a separate ring pass. */
#include <math.h>
#include <stdint.h>
#include <string.h>

enum {
    FUR, BELLY, EAR_IN, EYE, IRIS, PUPIL, NOSE, PAD, TONGUE, MOUTH, TOOTH,
    BLUSH, HORN, SNACK, DROP, CURL, WING, CATCH, NMAT
};

static float clampf(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static inline void shade_solid(
    int x, int y, float nx, float ny, float nz, int mat, const float *col,
    uint8_t *p
) {
    const float lx = -0.2999f, ly = 0.7998f, lz = 0.5199f;
    const float fx = 0.8173f, fy = 0.3269f, fz = 0.4755f;
    const float hx = -0.1674f, hy = 0.5021f, hz = 0.8480f;
    float ndl = nx * lx + ny * ly + nz * lz;
    float ndf = nx * fx + ny * fy + nz * fz;
    float wrap = clampf((ndl + 0.62f) / 1.62f, 0.0f, 1.0f);
    float diffuse, rim, ndh, r, g, b, ao, spec;
    int power, shiny;
    ndf = clampf(ndf, 0.0f, 1.0f);
    diffuse = clampf(0.34f + 0.72f * wrap + 0.16f * ndf, 0.0f, 1.22f);
    r = col[0] * diffuse;
    g = col[1] * diffuse;
    b = col[2] * diffuse;
    if (mat == IRIS) {
        float rad = sqrtf(nx * nx + ny * ny);
        float lower = ny < 0.0f ? -ny : 0.0f;
        float ring = rad * (1.0f - rad);
        float cr = col[0] * (1.0f - lower * 0.85f) + 0.45f * lower;
        float cg = col[1] * (1.0f - lower * 0.25f) + 0.84f * lower;
        float cb = col[2] * (1.0f - lower * 0.15f) + 0.98f * lower;
        float shade = 0.40f + rad * 0.90f;
        r = (cr * shade + ring * 0.40f) * (0.70f + 0.30f * diffuse);
        g = (cg * shade + ring * 0.22f) * (0.70f + 0.30f * diffuse);
        b = (cb * shade + ring * 0.50f) * (0.70f + 0.30f * diffuse);
        if (rad > 0.20f && rad < 0.82f) {
            unsigned hv = ((unsigned)x * 374761393u) ^ ((unsigned)y * 668265263u);
            if ((hv % 47u) == 0u) {
                p[0] = p[1] = p[2] = 255;
                p[3] = 255;
                return;
            }
        }
    }
    rim = 1.0f - nz;
    if (rim < 0.0f) rim = 0.0f;
    rim = rim * rim;
    if (mat == FUR || mat == WING || mat == BELLY || mat == BLUSH || mat == EAR_IN || mat == HORN || mat == CURL || mat == PAD) {
        r += rim * 0.18f;
        g += rim * 0.26f;
        b += rim * 0.34f;
        if (ny < 0.0f && (mat == FUR || mat == WING || mat == BELLY)) {
            float sh = -ny * 0.09f;
            r += sh * 0.50f;
            g += sh * 0.18f;
            b += sh * 0.22f;
        }
    }
    shiny = mat == EYE || mat == IRIS || mat == PUPIL || mat == NOSE || mat == TOOTH || mat == DROP || mat == HORN;
    if (shiny && mat != MOUTH) {
        ndh = nx * hx + ny * hy + nz * hz;
        if (ndh < 0.0f) ndh = 0.0f;
        power = mat == HORN ? 10 : (mat == IRIS ? 20 : 32);
        spec = powf(ndh, (float)power) * (mat == HORN ? 0.20f : (mat == IRIS ? 0.40f : 0.65f));
        r += spec;
        g += spec;
        b += spec;
    }
    ao = clampf(0.84f + 0.16f * ny, 0.70f, 1.0f);
    r = clampf(r * ao, 0.0f, 1.0f);
    g = clampf(g * ao, 0.0f, 1.0f);
    b = clampf(b * ao, 0.0f, 1.0f);
    p[0] = (uint8_t)(r * 255.0f);
    p[1] = (uint8_t)(g * 255.0f);
    p[2] = (uint8_t)(b * 255.0f);
    p[3] = 255;
}

void puff_splat(
    const float *sph, int count, const float *palette,
    uint8_t *out, float *zbuf, int w, int h,
    float body_sx, float body_sy
) {
    int i, x, y;
    (void)body_sx;
    (void)body_sy;
    memset(out, 0, (size_t)w * (size_t)h * 4);
    for (i = 0; i < w * h; i++) zbuf[i] = -1.0e6f;
    for (i = 0; i < count; i++) {
        float sx = sph[i * 6 + 0];
        float sy = sph[i * 6 + 1];
        float sz = sph[i * 6 + 2];
        float rp = sph[i * 6 + 3];
        float radius = sph[i * 6 + 4];
        int mat = (int)sph[i * 6 + 5];
        int x0, x1, y0, y1;
        const float *col;
        if (mat < 0 || mat >= NMAT || rp < 0.45f) continue;
        col = palette + mat * 3;
        x0 = (int)floorf(sx - rp) - 1;
        x1 = (int)ceilf(sx + rp) + 1;
        y0 = (int)floorf(sy - rp) - 1;
        y1 = (int)ceilf(sy + rp) + 1;
        if (x0 < 0) x0 = 0;
        if (y0 < 0) y0 = 0;
        if (x1 > w) x1 = w;
        if (y1 > h) y1 = h;
        for (y = y0; y < y1; y++) {
            for (x = x0; x < x1; x++) {
                float nx = ((float)x - sx) / rp;
                float ny = (sy - (float)y) / rp;
                float d2 = nx * nx + ny * ny;
                float nz, dep;
                int idx;
                if (d2 > 1.0f) continue;
                nz = sqrtf(1.0f - d2);
                dep = sz + nz * radius;
                idx = y * w + x;
                if (dep <= zbuf[idx]) continue;
                zbuf[idx] = dep;
                if (mat == CATCH) {
                    uint8_t hot = (uint8_t)(250.0f + nz * 5.0f);
                    out[idx * 4 + 0] = hot;
                    out[idx * 4 + 1] = hot;
                    out[idx * 4 + 2] = hot;
                    out[idx * 4 + 3] = 255;
                    continue;
                }
                shade_solid(x, y, nx, ny, nz, mat, col, out + idx * 4);
            }
        }
    }
}

void puff_caps(
    const float *cap, int count, const float *palette,
    uint8_t *out, float *zbuf, int w, int h
) {
    int i, x, y;
    for (i = 0; i < count; i++) {
        const float *c = cap + i * 11;
        float x0 = c[0], y0 = c[1], z0 = c[2];
        float x1 = c[3], y1 = c[4], z1 = c[5];
        float r0 = c[6], r1 = c[7], c0 = c[8], c1 = c[9];
        int mat = (int)c[10];
        float ax = x1 - x0, ay = y1 - y0, len2, maxr;
        int x0i, y0i, x1i, y1i;
        const float *col;
        if (mat < 0 || mat >= NMAT) continue;
        col = palette + mat * 3;
        len2 = ax * ax + ay * ay;
        if (len2 < 0.25f) continue;
        maxr = (r0 > r1 ? r0 : r1) + 1.5f;
        x0i = (int)floorf((x0 < x1 ? x0 : x1) - maxr);
        x1i = (int)ceilf((x0 > x1 ? x0 : x1) + maxr);
        y0i = (int)floorf((y0 < y1 ? y0 : y1) - maxr);
        y1i = (int)ceilf((y0 > y1 ? y0 : y1) + maxr);
        if (x0i < 0) x0i = 0;
        if (y0i < 0) y0i = 0;
        if (x1i > w) x1i = w;
        if (y1i > h) y1i = h;
        for (y = y0i; y < y1i; y++) {
            for (x = x0i; x < x1i; x++) {
                float t = (((float)x - x0) * ax + ((float)y - y0) * ay) / len2;
                float px, py, pz, rp, rc, dx, dy, d2, nx, ny, nz, dep;
                int idx;
                if (t < 0.0f) t = 0.0f;
                if (t > 1.0f) t = 1.0f;
                px = x0 + ax * t;
                py = y0 + ay * t;
                pz = z0 + (z1 - z0) * t;
                rp = r0 + (r1 - r0) * t;
                rc = c0 + (c1 - c0) * t;
                if (rp < 0.45f) continue;
                dx = (float)x - px;
                dy = py - (float)y;
                d2 = (dx * dx + dy * dy) / (rp * rp);
                if (d2 > 1.0f) continue;
                nz = sqrtf(1.0f - d2);
                dep = pz + nz * rc;
                idx = y * w + x;
                if (dep <= zbuf[idx]) continue;
                zbuf[idx] = dep;
                nx = dx / rp;
                ny = dy / rp;
                shade_solid(x, y, nx, ny, nz, mat, col, out + idx * 4);
            }
        }
    }
}

/* Ragged fur outside the body disc. Skips pixels the body or limbs already own. */
void puff_shell(
    float sx, float sy, float sz, float rp, float radius,
    float cr, float cg, float cb,
    uint8_t *out, float *zbuf, int w, int h
) {
    int x0, x1, y0, y1, x, y;
    float pad = rp * 1.74f;
    if (rp < 2.0f) return;
    x0 = (int)floorf(sx - pad);
    x1 = (int)ceilf(sx + pad);
    y0 = (int)floorf(sy - pad);
    y1 = (int)ceilf(sy + pad);
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > w) x1 = w;
    if (y1 > h) y1 = h;
    for (y = y0; y < y1; y++) {
        for (x = x0; x < x1; x++) {
            float dx = (float)x - sx;
            float dy = sy - (float)y;
            float dist2 = dx * dx + dy * dy;
            float edge, ang, n, dir, reach, t, a, len, nx, ny, nz;
            float wrap, r, g, b, ia, oa;
            int idx;
            uint8_t *p;
            if (dist2 < rp * rp || dist2 > pad * pad) continue;
            edge = sqrtf(dist2) / rp;
            ang = atan2f(dy, dx);
            len = sqrtf(dist2) + 1e-3f;
            dir = dy / len;
            /* Separate locks. Gaps stay bare so the coat reads as fur, not a glow. */
            /* A short uneven coat, longer on the crown. Wide lobes, not needles. */
            n = sinf(ang * 7.0f);
            if (n < 0.0f) {
                reach = 0.055f;
            } else {
                float u = n;
                reach = 0.055f + 0.11f * u * u;
                if (dir > 0.30f) reach += 0.16f * dir * u;
            }
            if (dir < -0.55f) reach = 0.02f;
            if (edge > 1.0f + reach) continue;
            t = (edge - 1.0f) / (reach + 1e-4f);
            if (t < 0.62f) a = 0.94f;
            else a = (1.0f - t) / 0.38f * 0.94f;
            if (a < 0.05f) continue;
            idx = y * w + x;
            p = out + idx * 4;
            if (p[3] > 36 && zbuf[idx] > sz) continue;
            nx = dx / len;
            ny = dy / len;
            wrap = clampf((nx * -0.30f + ny * 0.80f + 0.18f * 0.52f + 0.45f) / 1.45f, 0.15f, 1.0f);
            /* Stay in the fur colour. A white additive rim reads as a glow, not a coat. */
            r = cr * (0.82f + 0.18f * wrap) * (0.94f + 0.08f * (1.0f - t));
            g = cg * (0.82f + 0.18f * wrap) * (0.94f + 0.08f * (1.0f - t));
            b = cb * (0.82f + 0.18f * wrap) * (0.94f + 0.06f * (1.0f - t));
            r = clampf(r, 0.0f, 1.0f);
            g = clampf(g, 0.0f, 1.0f);
            b = clampf(b, 0.0f, 1.0f);
            ia = p[3] / 255.0f;
            oa = a + ia * (1.0f - a);
            p[0] = (uint8_t)(clampf((r * a + (p[0] / 255.0f) * ia * (1.0f - a)) / oa, 0.0f, 1.0f) * 255.0f);
            p[1] = (uint8_t)(clampf((g * a + (p[1] / 255.0f) * ia * (1.0f - a)) / oa, 0.0f, 1.0f) * 255.0f);
            p[2] = (uint8_t)(clampf((b * a + (p[2] / 255.0f) * ia * (1.0f - a)) / oa, 0.0f, 1.0f) * 255.0f);
            p[3] = (uint8_t)(clampf(oa, 0.0f, 1.0f) * 255.0f);
            (void)radius;
        }
    }
}

void puff_fur(
    const float *fib, int count, const float *palette,
    uint8_t *out, float *zbuf, int w, int h,
    float body_sx, float body_sy
) {
    (void)fib; (void)count; (void)palette; (void)out; (void)zbuf;
    (void)w; (void)h; (void)body_sx; (void)body_sy;
}
