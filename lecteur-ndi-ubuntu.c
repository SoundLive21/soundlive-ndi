/* SoundLive-NDI
 * Auteur : DUVERNOY Matthieu & ChatGPT
 * contact@soundlive.fr
 * Société SoundLive */
#define _POSIX_C_SOURCE 200809L
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <Processing.NDI.Lib.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { RATE = 48000, CHANNELS = 2, AUDIO_BLOCK = 480 };
static volatile sig_atomic_t stopping;

static void stop(int sig)
{
    (void)sig;
    stopping = 1;
}

static int list_sources(void)
{
    NDIlib_find_instance_t finder = NDIlib_find_create_v2(NULL);
    if (!finder) {
        fputs("Impossible de lancer la découverte NDI. Vérifie Avahi.\n", stderr);
        return 1;
    }

    const Uint64 deadline = SDL_GetTicks64() + 3000;
    while (!stopping && SDL_GetTicks64() < deadline)
        NDIlib_find_wait_for_sources(finder, 200);

    uint32_t count = 0;
    const NDIlib_source_t *sources = NDIlib_find_get_current_sources(finder, &count);
    for (uint32_t i = 0; i < count; ++i)
        if (sources[i].p_ndi_name)
            puts(sources[i].p_ndi_name);
    NDIlib_find_destroy(finder);
    return 0;
}

static void fill_audio(SDL_AudioDeviceID device, NDIlib_framesync_instance_t sync,
                       float gain)
{
    if (!device)
        return;

    const Uint32 bytes = AUDIO_BLOCK * CHANNELS * sizeof(float);
    for (Uint32 queued = SDL_GetQueuedAudioSize(device); queued < 3 * bytes; queued += bytes) {
        NDIlib_audio_frame_v2_t frame = {0};
        float samples[AUDIO_BLOCK * CHANNELS] = {0};
        NDIlib_framesync_capture_audio(sync, &frame, RATE, CHANNELS, AUDIO_BLOCK);
        if (frame.p_data && frame.no_samples == AUDIO_BLOCK && frame.no_channels == CHANNELS) {
            for (int channel = 0; channel < CHANNELS; ++channel) {
                const float *plane = (const float *)((const char *)frame.p_data
                                                   + channel * frame.channel_stride_in_bytes);
                for (int i = 0; i < AUDIO_BLOCK; ++i)
                    samples[i * CHANNELS + channel] = plane[i] * gain;
            }
        }
        if (frame.p_data)
            NDIlib_framesync_free_audio(sync, &frame);
        if (SDL_QueueAudio(device, samples, bytes) < 0)
            break;
    }
}

static SDL_Rect fitted_frame(int width, int height, const NDIlib_video_frame_v2_t *frame)
{
    const double aspect = frame->picture_aspect_ratio > 0 ?
                          frame->picture_aspect_ratio : (double)frame->xres / frame->yres;
    SDL_Rect rect = {0, 0, width, height};
    if (width > height * aspect)
        rect.w = SDL_max(1, (int)(height * aspect));
    else
        rect.h = SDL_max(1, (int)(width / aspect));
    rect.x = (width - rect.w) / 2;
    rect.y = (height - rect.h) / 2;
    return rect;
}

struct identification_badge {
    SDL_Texture *texture;
    Uint32 revision;
    int width;
    int height;
};

static void load_identification(SDL_Renderer *renderer, const char *path,
                                struct identification_badge *badge)
{
    SDL_RWops *file = SDL_RWFromFile(path, "rb");
    if (!file) {
        SDL_DestroyTexture(badge->texture);
        badge->texture = NULL;
        return;
    }
    const Uint32 width = SDL_ReadLE32(file);
    const Uint32 height = SDL_ReadLE32(file);
    const Uint32 revision = SDL_ReadLE32(file);
    if (width && width <= 600 && height && height <= 140 &&
        (!badge->texture || revision != badge->revision)) {
        const size_t length = width * height * 4;
        void *pixels = SDL_malloc(length);
        if (pixels && SDL_RWread(file, pixels, 1, length) == length) {
            SDL_Texture *texture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGBA32,
                                                     SDL_TEXTUREACCESS_STATIC, width, height);
            if (texture && SDL_UpdateTexture(texture, NULL, pixels, width * 4) == 0 &&
                SDL_SetTextureBlendMode(texture, SDL_BLENDMODE_BLEND) == 0) {
                SDL_DestroyTexture(badge->texture);
                *badge = (struct identification_badge){texture, revision, width, height};
            } else {
                SDL_DestroyTexture(texture);
            }
        }
        SDL_free(pixels);
    }
    SDL_RWclose(file);
}

static int display_frame(SDL_Renderer *renderer, SDL_Texture **texture,
                         const struct identification_badge *badge,
                         const NDIlib_video_frame_v2_t *frame)
{
    int width = 0, height = 0;
    if (frame->FourCC != NDIlib_FourCC_video_type_BGRX &&
        frame->FourCC != NDIlib_FourCC_video_type_BGRA) {
        fputs("Format vidéo NDI inattendu.\n", stderr);
        return -1;
    }
    if (*texture)
        SDL_QueryTexture(*texture, NULL, NULL, &width, &height);
    if (width != frame->xres || height != frame->yres) {
        SDL_DestroyTexture(*texture);
        *texture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_BGRA32,
                                     SDL_TEXTUREACCESS_STREAMING, frame->xres, frame->yres);
        if (!*texture)
            return -1;
        SDL_SetTextureBlendMode(*texture, SDL_BLENDMODE_NONE);
    }
    if (SDL_UpdateTexture(*texture, NULL, frame->p_data, frame->line_stride_in_bytes) < 0)
        return -1;

    int output_width, output_height;
    if (SDL_GetRendererOutputSize(renderer, &output_width, &output_height) < 0)
        return -1;
    const SDL_Rect dst = fitted_frame(output_width, output_height, frame);
    if (SDL_RenderClear(renderer) < 0 || SDL_RenderCopy(renderer, *texture, NULL, &dst) < 0)
        return -1;
    if (badge->texture) {
        const int margin = SDL_max(8, output_height / 60);
        const double scale = SDL_min(output_height / 900.0,
                                     (output_width - 2 * margin) / (double)badge->width);
        const int width = SDL_max(1, (int)(badge->width * scale));
        const int height = SDL_max(1, (int)(badge->height * scale));
        const SDL_Rect corner = {output_width - width - margin, output_height - height - margin, width, height};
        SDL_RenderCopy(renderer, badge->texture, NULL, &corner);
    }
    SDL_RenderPresent(renderer);
    return 0;
}

static FILE *open_ready(const char *path)
{
#ifdef _WIN32
    wchar_t *wide = (wchar_t *)SDL_iconv_string("UTF-16LE", "UTF-8", path, strlen(path) + 1);
    FILE *file = wide ? _wfopen(wide, L"w") : NULL;
    SDL_free(wide);
    return file;
#else
    return fopen(path, "w");
#endif
}

#ifndef _WIN32
/* On demand only. Use the very same frame and aspect ratio as the SDL player. */
static void preview_frame(const char *base, const NDIlib_video_frame_v2_t *frame,
                          int out_width, int out_height)
{
    char request[4096], pending[4096], output[4096], token[25];
    if (snprintf(request, sizeof(request), "%s.request", base) >= (int)sizeof(request) ||
        snprintf(pending, sizeof(pending), "%s.tmp", base) >= (int)sizeof(pending) ||
        snprintf(output, sizeof(output), "%s.rgb", base) >= (int)sizeof(output))
        return;
    FILE *file = fopen(request, "r");
    if (!file)
        return;
    const int parsed = fscanf(file, "%24[0123456789abcdef]", token);
    fclose(file);
    remove(request);
    if (parsed != 1 || strlen(token) != 24 || out_width <= 0 || out_height <= 0 ||
        frame->line_stride_in_bytes < 4LL * frame->xres)
        return;
    const double scale_x = 240.0 / out_width, scale_y = 135.0 / out_height;
    const double scale = scale_x < scale_y ? scale_x : scale_y;
    const int width = SDL_max(1, (int)(out_width * scale + 0.5));
    const int height = SDL_max(1, (int)(out_height * scale + 0.5));
    const SDL_Rect rect = fitted_frame(width, height, frame);
    file = fopen(pending, "wb");
    if (!file)
        return;
    fprintf(file, "%s\n%d %d\n", token, width, height);
    for (int y = 0; y < height; ++y) {
        unsigned char line[240 * 3] = {0};
        if (y >= rect.y && y < rect.y + rect.h) {
            const unsigned char *row = frame->p_data +
                (size_t)((int64_t)(y - rect.y) * frame->yres / rect.h) * frame->line_stride_in_bytes;
            for (int x = 0; x < rect.w; ++x) {
                const unsigned char *pixel = row + 4 * ((int64_t)x * frame->xres / rect.w);
                unsigned char *rgb = &line[(rect.x + x) * 3];
                rgb[0] = pixel[2];
                rgb[1] = pixel[1];
                rgb[2] = pixel[0];
            }
        }
        if (fwrite(line, 3, width, file) != (size_t)width)
            break;
    }
    const bool complete = !ferror(file);
    if (fclose(file) == 0 && complete)
        rename(pending, output);
    remove(pending);
}
#endif

static int play(const char *source, bool low, float volume, const char *ready_file)
{
    NDIlib_find_instance_t finder = NDIlib_find_create_v2(NULL);
    if (!finder) {
        fputs("Impossible de lancer la découverte NDI.\n", stderr);
        return 1;
    }

    const NDIlib_source_t *selected = NULL;
    const Uint64 deadline = SDL_GetTicks64() + 4000;
    while (!selected && !stopping && SDL_GetTicks64() < deadline) {
        uint32_t count = 0;
        const NDIlib_source_t *sources = NDIlib_find_get_current_sources(finder, &count);
        for (uint32_t i = 0; i < count; ++i) {
            if (sources[i].p_ndi_name && strcmp(source, sources[i].p_ndi_name) == 0) {
                selected = &sources[i];
                break;
            }
        }
        if (!selected)
            NDIlib_find_wait_for_sources(finder, 200);
    }
    if (!selected) {
        NDIlib_find_destroy(finder);
        return 0;
    }

    const NDIlib_recv_create_v3_t settings = {
        .source_to_connect_to = *selected,
        .color_format = NDIlib_recv_color_format_BGRX_BGRA,
        .bandwidth = low ? NDIlib_recv_bandwidth_lowest : NDIlib_recv_bandwidth_highest,
        .allow_video_fields = false,
    };
    NDIlib_recv_instance_t receiver = NDIlib_recv_create_v3(&settings);
    NDIlib_find_destroy(finder);
    if (!receiver) {
        fputs("Impossible de créer le récepteur NDI.\n", stderr);
        return 1;
    }

    NDIlib_framesync_instance_t sync = NDIlib_framesync_create(receiver);
    if (!sync) {
        fputs("Impossible de synchroniser le flux NDI.\n", stderr);
        NDIlib_recv_destroy(receiver);
        return 1;
    }

    int result = 1;
    SDL_Window *window = NULL;
    SDL_Renderer *renderer = NULL;
    SDL_Texture *texture = NULL;
    SDL_AudioDeviceID audio = 0;
    bool visible = false;
    bool paused = true;
    int64_t received = 0;
    Uint64 last_frame = SDL_GetTicks64();
    Uint64 next_draw = 0;
    int refresh_rate = 60;
    const char *identification = getenv("SOUNDLIVE_IDENTIFICATION_TEXTURE");
    struct identification_badge badge = {0};
    Uint64 next_identification = 0;
#ifndef _WIN32
    const char *preview = getenv("SOUNDLIVE_RENDER_PREVIEW");
    Uint64 next_preview = 0;
#endif

    if (SDL_InitSubSystem(SDL_INIT_VIDEO) < 0)
        goto sdl_error;
    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "linear");
    int position = SDL_WINDOWPOS_CENTERED;
    const char *requested_display = getenv("SOUNDLIVE_DISPLAY_INDEX");
    if (requested_display) {
        char *end = NULL;
        long index = strtol(requested_display, &end, 10);
        if (end == requested_display || *end || index < 0 || index >= SDL_GetNumVideoDisplays()) {
            fprintf(stderr, "Sortie vidéo indisponible.\n");
            goto cleanup;
        }
        position = SDL_WINDOWPOS_CENTERED_DISPLAY((int)index);
    }
    window = SDL_CreateWindow("SoundLive-NDI", position, position,
                              1280, 720, SDL_WINDOW_HIDDEN | SDL_WINDOW_ALLOW_HIGHDPI);
    if (!window)
        goto sdl_error;
    SDL_DisplayMode mode = {0};
    if (SDL_GetCurrentDisplayMode(SDL_GetWindowDisplayIndex(window), &mode) == 0 &&
        mode.refresh_rate > 0)
        refresh_rate = mode.refresh_rate;

    renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!renderer)
        renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    if (!renderer)
        goto sdl_error;
    SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255);

    if (SDL_InitSubSystem(SDL_INIT_AUDIO) == 0) {
        const SDL_AudioSpec wanted = {.freq = RATE, .format = AUDIO_F32SYS,
                                      .channels = CHANNELS, .samples = 512};
        audio = SDL_OpenAudioDevice(NULL, 0, &wanted, NULL, 0);
    }
    if (!audio)
        fprintf(stderr, "Sortie audio indisponible : %s\n", SDL_GetError());

    while (!stopping) {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_QUIT ||
                (event.type == SDL_KEYDOWN && event.key.keysym.sym == SDLK_ESCAPE))
                goto finished;
        }

        const Uint64 now = SDL_GetTicks64();
        if (identification && now >= next_identification) {
            load_identification(renderer, identification, &badge);
            next_identification = now + 250;
        }
        NDIlib_recv_performance_t performance = {0}, dropped = {0};
        NDIlib_recv_get_performance(receiver, &performance, &dropped);
        if (performance.video_frames != received) {
            received = performance.video_frames;
            last_frame = now;
        } else if (now - last_frame > (visible ? 4000 : 12000)) {
            fputs("Flux vidéo NDI absent.\n", stderr);
            goto finished;
        }

        if (visible) {
            fill_audio(audio, sync, volume);
            if (audio && paused) {
                SDL_PauseAudioDevice(audio, 0);
                paused = false;
            }
        }

        if (now >= next_draw) {
            next_draw += 1000 / refresh_rate;
            if (next_draw <= now)
                next_draw = now + 1000 / refresh_rate;
            NDIlib_video_frame_v2_t frame = {0};
            NDIlib_framesync_capture_video(sync, &frame, NDIlib_frame_format_type_progressive);
            if (frame.p_data && frame.xres > 0 && frame.yres > 0) {
                if (!visible) {
                    SDL_ShowWindow(window);
                    if (SDL_SetWindowFullscreen(window, SDL_WINDOW_FULLSCREEN_DESKTOP) < 0) {
                        NDIlib_framesync_free_video(sync, &frame);
                        goto sdl_error;
                    }
                    SDL_ShowCursor(SDL_DISABLE);
                }
                const int displayed = display_frame(renderer, &texture, &badge, &frame);
#ifndef _WIN32
                if (displayed == 0 && preview && now >= next_preview) {
                    int width, height;
                    if (SDL_GetRendererOutputSize(renderer, &width, &height) == 0)
                        preview_frame(preview, &frame, width, height);
                    next_preview = now + 250;
                }
#endif
                NDIlib_framesync_free_video(sync, &frame);
                if (displayed < 0)
                    goto sdl_error;
                if (!visible) {
                    FILE *file = open_ready(ready_file);
                    if (!file) {
                        perror("Impossible de signaler la lecture");
                        goto cleanup;
                    }
                    fclose(file);
                    visible = true;
                }
            } else if (frame.p_data) {
                NDIlib_framesync_free_video(sync, &frame);
            }
        }
        SDL_Delay(2);
    }

finished:
    result = 0;
    goto cleanup;

sdl_error:
    fprintf(stderr, "Affichage : %s\n", SDL_GetError());

cleanup:
    if (audio)
        SDL_CloseAudioDevice(audio);
    SDL_DestroyTexture(texture);
    SDL_DestroyTexture(badge.texture);
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    NDIlib_framesync_destroy(sync);
    NDIlib_recv_destroy(receiver);
    return result;
}

static int run(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "--displays") == 0) {
        SDL_SetMainReady();
        if (SDL_Init(SDL_INIT_VIDEO) < 0) return 1;
        int count = SDL_GetNumVideoDisplays();
        for (int i = 0; i < count; ++i) {
            SDL_Rect rect;
            if (SDL_GetDisplayBounds(i, &rect) == 0)
                printf("%d %d %d %d %d\n", i, rect.x, rect.y, rect.w, rect.h);
        }
        SDL_Quit();
        return count < 0 ? 1 : 0;
    }
    if (argc == 2 && strcmp(argv[1], "--identification") == 0) {
        puts("identification");
        return 0;
    }
#ifndef _WIN32
    if (argc == 2 && strcmp(argv[1], "--apercu-interne") == 0) {
        puts("apercu-interne");
        return 0;
    }
#endif
    const bool list = argc == 2 && strcmp(argv[1], "--sources") == 0;
    char *end = NULL;
    const long volume = argc == 5 ? strtol(argv[3], &end, 10) : -1;
    if (!list && (argc != 5 || (strcmp(argv[2], "0") != 0 && strcmp(argv[2], "1") != 0)
                  || end == argv[3] || *end || volume < 0 || volume > 100)) {
        fputs("Usage : lecteur-ndi --sources | SOURCE LOW VOLUME FICHIER_ETAT\n", stderr);
        return 2;
    }

    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    SDL_SetMainReady();
    if (SDL_Init(SDL_INIT_TIMER) < 0) {
        fprintf(stderr, "%s\n", SDL_GetError());
        return 1;
    }
    if (!NDIlib_initialize()) {
        fputs("Impossible d’initialiser le runtime NDI.\n", stderr);
        SDL_Quit();
        return 1;
    }
    const int result = list ? list_sources() :
                       play(argv[1], strcmp(argv[2], "1") == 0, volume / 100.0f, argv[4]);
    NDIlib_destroy();
    SDL_Quit();
    return result;
}

#ifdef _WIN32
#include <wchar.h>

int wmain(int argc, wchar_t **wide)
{
    if (argc != 2 && argc != 5)
        return 2;
    char *argv[5] = {0};
    int result = 2;
    for (int i = 0; i < argc; ++i) {
        argv[i] = SDL_iconv_string("UTF-8", "UTF-16LE", (const char *)wide[i],
                                   (wcslen(wide[i]) + 1) * sizeof(wchar_t));
        if (!argv[i])
            goto end;
    }
    result = run(argc, argv);
end:
    for (int i = 0; i < argc; ++i)
        SDL_free(argv[i]);
    return result;
}
#else
int main(int argc, char **argv)
{
    return run(argc, argv);
}
#endif
