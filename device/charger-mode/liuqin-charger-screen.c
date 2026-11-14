// SPDX-License-Identifier: GPL-2.0-only
/* Charging status on the connected DSI panel; no persistent files. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <drm.h>
#include <drm_mode.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <poll.h>
#include <sys/inotify.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include "font-8x16.h"

static unsigned char *pixels;
static unsigned width, height, stride;

static void pixel(unsigned x, unsigned y, unsigned r, unsigned g, unsigned b)
{
    if (x >= width || y >= height) return;
    uint32_t *p = (uint32_t *)(pixels + y * stride + x * 4);
    *p = (r << 16) | (g << 8) | b;
}

static void rect(unsigned x, unsigned y, unsigned w, unsigned h,
                 unsigned r, unsigned g, unsigned b)
{
    for (unsigned j = y; j < y + h && j < height; j++)
        for (unsigned i = x; i < x + w && i < width; i++) pixel(i,j,r,g,b);
}

static void text(unsigned y, const char *s, unsigned scale,
                 unsigned r, unsigned g, unsigned b)
{
    unsigned size = strlen(s) * 8 * scale;
    unsigned x = size < width ? (width - size) / 2 : 0;
    for (; *s; s++, x += 8 * scale) {
        const unsigned char *glyph = charge_font + (unsigned char)*s * 16;
        for (unsigned j = 0; j < 16; j++)
            for (unsigned i = 0; i < 8; i++)
                if (glyph[j] & (0x80 >> i))
                    rect(x+i*scale,y+j*scale,scale,scale,r,g,b);
    }
}

static void draw(const char *capacity_text, const char *trend,
                 const char *can_boot, const char *usb)
{
    rect(0,0,width,height,0,0,0);
    unsigned scale=width/(8*44);
    if (scale > height/(16*18)) scale=height/(16*18);
    if (scale < 1) scale=1;
    unsigned line=20*scale, y=height/12;
    text(y,"BATTERY",scale,160,160,160); y+=line*2;
    char percent[16];
    char *end; long capacity=strtol(capacity_text,&end,10);
    int known=*capacity_text && !*end && capacity>=0 && capacity<=100;
    if (known) snprintf(percent,sizeof(percent),"%ld%%",capacity);
    else strcpy(percent,"--");
    text(y,percent,scale*2,235,235,235); y+=line*3;
    const char *status="MEASURING CHARGE";
    if (!strcmp(trend,"gaining")) status="BATTERY IS CHARGING";
    else if (!strcmp(trend,"not-gaining")) status="USE A WALL CHARGER";
    else if (!strcmp(trend,"full")) status="BATTERY FULL";
    else if (!strcmp(trend,"unknown")) status="CHARGE DATA UNAVAILABLE";
    text(y,status,scale,100,210,140); y+=line*2;
    text(y,!strcmp(can_boot,"yes") ? "PRESS POWER TO START" :
         (known && capacity<40 ? "START REQUIRES 40% BATTERY" :
          "WAIT FOR SAFE BATTERY READING"),scale,210,210,210); y+=line*2;
    text(y,"HOLD POWER 2s FOR FASTBOOT",scale,180,180,180); y+=line*2;
    text(y,!strcmp(usb,"ready") ? "USB STATUS AVAILABLE" :
         "USB STATUS NOT CONNECTED",scale,130,130,130);
}

static int open_panel(void)
{
    for (unsigned n=0; n<16; n++) {
        char path[64]; snprintf(path,sizeof(path),"/dev/dri/card%u",n);
        int fd=open(path,O_RDWR|O_CLOEXEC);
        if (fd < 0) continue;
        drmVersionPtr version=drmGetVersion(fd);
        int is_msm=version && version->name && !strcmp(version->name,"msm");
        drmFreeVersion(version);
        if (!is_msm || drmSetMaster(fd)) { close(fd); continue; }
        drmModeRes *res=drmModeGetResources(fd);
        if (!res) { close(fd); continue; }
        for (int i=0; i<res->count_connectors; i++) {
            drmModeConnector *c=drmModeGetConnector(fd,res->connectors[i]);
            if (!c) continue;
            if (c->connection != DRM_MODE_CONNECTED || !c->count_modes ||
                c->connector_type != DRM_MODE_CONNECTOR_DSI) { drmModeFreeConnector(c); continue; }
            uint32_t crtc=0;
            for (int j=0; j<c->count_encoders && !crtc; j++) {
                drmModeEncoder *e=drmModeGetEncoder(fd,c->encoders[j]);
                if (!e) continue;
                for (int k=0; k<res->count_crtcs && k<32; k++)
                    if (e->possible_crtcs & (1u<<k)) { crtc=res->crtcs[k]; break; }
                drmModeFreeEncoder(e);
            }
            drmModeModeInfo mode=c->modes[0];
            for (int j=0; j<c->count_modes; j++)
                if (c->modes[j].type & DRM_MODE_TYPE_PREFERRED) { mode=c->modes[j]; break; }
            width=mode.hdisplay; height=mode.vdisplay;
            if (!crtc || width<320 || height<320 || width>4096 || height>4096) {
                drmModeFreeConnector(c); continue;
            }
            struct drm_mode_create_dumb create={.width=width,.height=height,.bpp=32};
            if (drmIoctl(fd,DRM_IOCTL_MODE_CREATE_DUMB,&create)) { drmModeFreeConnector(c); continue; }
            uint32_t fb=0;
            if (drmModeAddFB(fd,width,height,24,32,create.pitch,create.handle,&fb)) {
                drmModeFreeConnector(c); close(fd); drmModeFreeResources(res); return -1;
            }
            struct drm_mode_map_dumb map={.handle=create.handle};
            if (drmIoctl(fd,DRM_IOCTL_MODE_MAP_DUMB,&map)) {
                drmModeFreeConnector(c); close(fd); drmModeFreeResources(res); return -1;
            }
            stride=create.pitch;
            pixels=mmap(NULL,create.size,PROT_READ|PROT_WRITE,MAP_SHARED,fd,map.offset);
            if (pixels==MAP_FAILED) { drmModeFreeConnector(c); close(fd); drmModeFreeResources(res); return -1; }
            draw("NA","measuring","no","unavailable");
            int rc=drmModeSetCrtc(fd,crtc,fb,0,0,&c->connector_id,1,&mode);
            drmModeFreeConnector(c); drmModeFreeResources(res);
            if (rc) { munmap(pixels,create.size); close(fd); return -1; }
            return fd;
        }
        drmModeFreeResources(res); close(fd);
    }
    return -1;
}

int main(int argc, char **argv)
{
    if (argc==7 && !strcmp(argv[1],"--ppm")) {
        width=1280; height=800; stride=width*4;
        pixels=calloc(height,stride); if (!pixels) return 1;
        draw(argv[3],argv[4],argv[5],argv[6]);
        FILE *out=fopen(argv[2],"wb"); if (!out) return 1;
        fprintf(out,"P6\n%u %u\n255\n",width,height);
        for (unsigned y=0; y<height; y++) for (unsigned x=0; x<width; x++) {
            uint32_t v=*(uint32_t *)(pixels+y*stride+x*4);
            unsigned char rgb[]={v>>16,v>>8,v}; fwrite(rgb,1,3,out);
        }
        fclose(out); free(pixels); return 0;
    }
    if (argc!=2) { fprintf(stderr,"usage: %s STATUS_DIRECTORY\n",argv[0]); return 64; }
    int events=inotify_init1(IN_CLOEXEC);
    if (events<0 || inotify_add_watch(events,argv[1],IN_MOVED_TO|IN_CLOSE_WRITE)<0) return 1;
    int display=open_panel();
    if (display<0) { perror("charging DSI scanout"); return 1; }
    char path[4096];
    if (snprintf(path,sizeof(path),"%s/screen.txt",argv[1]) >= (int)sizeof(path)) return 1;
    for (;;) {
        char capacity[16],trend[32],boot[8],usb[32];
        FILE *state=fopen(path,"r");
        if (state) {
            if (fscanf(state,"%15s %31s %7s %31s",capacity,trend,boot,usb)==4)
                draw(capacity,trend,boot,usb);
            fclose(state);
        }
        struct pollfd p={.fd=events,.events=POLLIN};
        if (poll(&p,1,-1)<0 && errno!=EINTR) return 1;
        char buffer[4096];
        if (p.revents & POLLIN) { ssize_t count=read(events,buffer,sizeof(buffer)); if (count<0 && errno!=EINTR) return 1; }
    }
}
