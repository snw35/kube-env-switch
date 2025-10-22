FROM alpine:latest

ENV FIX_ME 0

# Image to simulate a crashloop

CMD ["sh","-c","if [ \"$FIX_ME\" = \"1\" ]; then sleep 3600; else exit 1; fi"]
